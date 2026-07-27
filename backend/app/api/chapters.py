"""章节管理API"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import json
import asyncio
from typing import Optional
from datetime import datetime
from asyncio import Queue, Lock

from app.database import get_db
from app.models.chapter import Chapter
from app.models.project import Project
from app.models.outline import Outline
from app.models.character import Character
from app.models.generation_history import GenerationHistory
from app.models.writing_style import WritingStyle
from app.models.analysis_task import AnalysisTask
from app.models.memory import PlotAnalysis, StoryMemory
from app.models.batch_generation_task import BatchGenerationTask
from app.models.regeneration_task import RegenerationTask
from app.schemas.chapter import (
    ChapterCreate,
    ChapterUpdate,
    ChapterResponse,
    ChapterListResponse,
    ChapterGenerateRequest,
    BatchGenerateRequest,
    BatchGenerateResponse,
    BatchGenerateStatusResponse
)
from app.schemas.regeneration import (
    ChapterRegenerateRequest,
    RegenerationTaskResponse,
    RegenerationTaskStatus
)
from app.services.ai_service import AIService
from app.services.prompt_service import prompt_service
from app.services.plot_analyzer import PlotAnalyzer
from app.services.memory_service import memory_service
from app.services.corpus_bridge import CorpusBridge
from app.services.chapter_regenerator import ChapterRegenerator
from app.logger import get_logger
from app.api.settings import get_user_ai_service
from app.utils.chapter_generation_logging import (
    log_chapter_generation_step,
    log_final_chapter_prompt,
)
from app.utils.chapter_prompt import append_one_time_prompt
from app.utils.sse_response import create_sse_response

router = APIRouter(prefix="/chapters", tags=["章节管理"])
logger = get_logger(__name__)

# 全局数据库写入锁（每个用户一个锁，用于保护SQLite写入操作）
db_write_locks: dict[str, Lock] = {}


async def verify_project_access(project_id: str, user_id: str, db: AsyncSession) -> Project:
    """
    验证用户是否有权访问指定项目
    
    参数：
        project_id: 项目ID
        user_id: 用户ID
        db: 数据库会话
        
    返回：
        Project: 项目对象
        
    抛出：
        HTTPException: 401 未登录，404 项目不存在或无权访问
    """
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == user_id
        )
    )
    project = result.scalar_one_or_none()
    
    if not project:
        logger.warning(f"项目访问被拒绝: project_id={project_id}, user_id={user_id}")
        raise HTTPException(status_code=404, detail="项目不存在或无权访问")
    
    return project


async def get_db_write_lock(user_id: str) -> Lock:
    """获取或创建用户的数据库写入锁"""
    if user_id not in db_write_locks:
        db_write_locks[user_id] = Lock()
        logger.debug(f"🔒 为用户 {user_id} 创建数据库写入锁")
    return db_write_locks[user_id]


@router.post("", response_model=ChapterResponse, summary="创建章节")
async def create_chapter(
    chapter: ChapterCreate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """创建新的章节"""
    # 验证用户权限和项目是否存在
    user_id = getattr(request.state, 'user_id', None)
    project = await verify_project_access(chapter.project_id, user_id, db)
    
    # 计算字数
    word_count = len(chapter.content)
    
    db_chapter = Chapter(
        **chapter.model_dump(),
        word_count=word_count
    )
    db.add(db_chapter)
    
    # 更新项目的当前字数
    project.current_words = project.current_words + word_count
    
    await db.commit()
    await db.refresh(db_chapter)
    return db_chapter


@router.get("/project/{project_id}", response_model=ChapterListResponse, summary="获取项目的所有章节")
async def get_project_chapters(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """获取指定项目的所有章节（路径参数版本）"""
    # 验证用户权限
    user_id = getattr(request.state, 'user_id', None)
    await verify_project_access(project_id, user_id, db)
    
    # 获取总数
    count_result = await db.execute(
        select(func.count(Chapter.id)).where(Chapter.project_id == project_id)
    )
    total = count_result.scalar_one()
    
    # 获取章节列表
    result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )
    chapters = result.scalars().all()
    
    return ChapterListResponse(total=total, items=chapters)


@router.get("/{chapter_id}", response_model=ChapterResponse, summary="获取章节详情")
async def get_chapter(
    chapter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """根据ID获取章节详情"""
    result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    chapter = result.scalar_one_or_none()
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    # 验证用户权限
    user_id = getattr(request.state, 'user_id', None)
    await verify_project_access(chapter.project_id, user_id, db)
    
    return chapter


@router.get("/{chapter_id}/navigation", summary="获取章节导航信息")
async def get_chapter_navigation(
    chapter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    获取章节的导航信息（上一章/下一章）
    用于章节阅读器的翻页功能
    """
    # 获取当前章节
    result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    current_chapter = result.scalar_one_or_none()
    
    if not current_chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    # 验证用户权限
    user_id = getattr(request.state, 'user_id', None)
    await verify_project_access(current_chapter.project_id, user_id, db)
    
    # 获取上一章
    prev_result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == current_chapter.project_id)
        .where(Chapter.chapter_number < current_chapter.chapter_number)
        .order_by(Chapter.chapter_number.desc())
        .limit(1)
    )
    prev_chapter = prev_result.scalar_one_or_none()
    
    # 获取下一章
    next_result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == current_chapter.project_id)
        .where(Chapter.chapter_number > current_chapter.chapter_number)
        .order_by(Chapter.chapter_number.asc())
        .limit(1)
    )
    next_chapter = next_result.scalar_one_or_none()
    
    return {
        "current": {
            "id": current_chapter.id,
            "chapter_number": current_chapter.chapter_number,
            "title": current_chapter.title
        },
        "previous": {
            "id": prev_chapter.id,
            "chapter_number": prev_chapter.chapter_number,
            "title": prev_chapter.title
        } if prev_chapter else None,
        "next": {
            "id": next_chapter.id,
            "chapter_number": next_chapter.chapter_number,
            "title": next_chapter.title
        } if next_chapter else None
    }


@router.put("/{chapter_id}", response_model=ChapterResponse, summary="更新章节")
async def update_chapter(
    chapter_id: str,
    chapter_update: ChapterUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """更新章节信息"""
    result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    chapter = result.scalar_one_or_none()
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    # 验证用户权限
    user_id = getattr(request.state, 'user_id', None)
    await verify_project_access(chapter.project_id, user_id, db)
    
    # 记录旧字数
    old_word_count = chapter.word_count or 0
    
    # 更新字段
    update_data = chapter_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(chapter, field, value)
    
    # 如果内容更新了，重新计算字数
    if "content" in update_data and chapter.content:
        new_word_count = len(chapter.content)
        chapter.word_count = new_word_count
        
        # 更新项目字数
        result = await db.execute(
            select(Project).where(Project.id == chapter.project_id)
        )
        project = result.scalar_one_or_none()
        if project:
            project.current_words = project.current_words - old_word_count + new_word_count
    
    await db.commit()
    await db.refresh(chapter)
    return chapter


@router.delete("/{chapter_id}", summary="删除章节")
async def delete_chapter(
    chapter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """删除章节"""
    result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    chapter = result.scalar_one_or_none()
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    # 验证用户权限
    user_id = getattr(request.state, 'user_id', None)
    await verify_project_access(chapter.project_id, user_id, db)
    
    # 更新项目字数
    result = await db.execute(
        select(Project).where(Project.id == chapter.project_id)
    )
    project = result.scalar_one_or_none()
    if project:
        project.current_words = max(0, project.current_words - chapter.word_count)
    
    await db.delete(chapter)
    await db.commit()
    
    return {"message": "章节删除成功"}


async def check_prerequisites(db: AsyncSession, chapter: Chapter) -> tuple[bool, str, list[Chapter]]:
    """
    检查章节前置条件
    
    参数：
        db: 数据库会话
        chapter: 当前章节
        
    返回：
        (可否生成, 错误信息, 前置章节列表)
    """
    # 如果是第一章，无需检查前置
    if chapter.chapter_number == 1:
        return True, "", []
    
    # 查询所有前置章节（序号小于当前章节的）
    result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == chapter.project_id)
        .where(Chapter.chapter_number < chapter.chapter_number)
        .order_by(Chapter.chapter_number)
    )
    previous_chapters = result.scalars().all()
    
    # 检查是否所有前置章节都有内容
    incomplete_chapters = [
        ch for ch in previous_chapters
        if not ch.content or ch.content.strip() == ""
    ]
    
    if incomplete_chapters:
        missing_numbers = [str(ch.chapter_number) for ch in incomplete_chapters]
        error_msg = f"需要先完成前置章节：第 {', '.join(missing_numbers)} 章"
        return False, error_msg, previous_chapters
    
    return True, "", previous_chapters


async def build_smart_chapter_context(
    db: AsyncSession,
    project_id: str,
    current_chapter_number: int,
    user_id: str
) -> dict:
    """
    智能构建章节生成上下文（支持海量章节场景）
    
    策略：
    1. 故事骨架：每50章采样1章（标题+摘要）
    2. 相关历史：通过chapter_summary记忆语义检索15个最相关章节
    3. 近期概要：最近30章的简要摘要（200字/章）
    4. 最近完整：最近3章的完整内容
    
    参数：
        db: 数据库会话
        project_id: 项目ID
        current_chapter_number: 当前章节序号
        user_id: 用户ID
        
    返回：
        包含各部分上下文的字典
    """
    context_parts = {
        'story_skeleton': '',      # 故事骨架
        'relevant_history': '',    # 相关历史章节
        'recent_summary': '',      # 近期概要
        'recent_full': '',         # 最近完整内容
        'stats': {}                # 统计信息
    }
    
    try:
        # 1. 获取所有已完成的前置章节（只取ID和序号）
        all_chapters_result = await db.execute(
            select(Chapter.id, Chapter.chapter_number, Chapter.title)
            .where(Chapter.project_id == project_id)
            .where(Chapter.chapter_number < current_chapter_number)
            .where(Chapter.content != None)
            .where(Chapter.content != "")
            .order_by(Chapter.chapter_number)
        )
        all_chapters_info = all_chapters_result.all()
        total_previous = len(all_chapters_info)
        
        if total_previous == 0:
            logger.info("📚 这是第一章，无需构建前置上下文")
            return context_parts
        
        logger.info(f"📚 开始构建智能上下文：共{total_previous}章前置内容")
        
        # 2. 构建故事骨架（每50章采样）
        skeleton_chapters = []
        if total_previous > 50:
            sample_interval = 50
            skeleton_indices = list(range(0, total_previous, sample_interval))
            
            for idx in skeleton_indices:
                chapter_info = all_chapters_info[idx]
                # 获取章节摘要（优先从chapter_summary记忆获取）
                summary_result = await db.execute(
                    select(StoryMemory.content)
                    .where(StoryMemory.project_id == project_id)
                    .where(StoryMemory.chapter_id == chapter_info.id)
                    .where(StoryMemory.memory_type == 'chapter_summary')
                    .limit(1)
                )
                summary_row = summary_result.scalar_one_or_none()
                summary = summary_row if summary_row else "（无摘要）"
                
                skeleton_chapters.append({
                    'number': chapter_info.chapter_number,
                    'title': chapter_info.title,
                    'summary': summary
                })
            
            context_parts['story_skeleton'] = "【故事骨架】\n" + "\n".join([
                f"第{ch['number']}章《{ch['title']}》：{ch['summary']}"
                for ch in skeleton_chapters
            ])
            logger.info(f"  ✅ 故事骨架：采样{len(skeleton_chapters)}章（每50章1个）")
        
        # 3. 语义检索相关历史章节（使用chapter_summary记忆）
        # 获取当前章节的大纲作为查询
        current_outline_result = await db.execute(
            select(Outline.content)
            .where(Outline.project_id == project_id)
            .where(Outline.order_index == current_chapter_number)
        )
        current_outline = current_outline_result.scalar_one_or_none()
        
        if current_outline and total_previous > 3:
            # 使用记忆服务进行语义检索
            relevant_memories = await memory_service.search_memories(
                user_id=user_id,
                project_id=project_id,
                query=current_outline,
                memory_types=['chapter_summary'],
                limit=15,  # 检索15个最相关的章节
                min_importance=0.0  # 不过滤重要性，依赖语义相关度
            )
            
            if relevant_memories:
                relevant_chapters_text = []
                for mem in relevant_memories:
                    # 获取章节信息
                    chapter_result = await db.execute(
                        select(Chapter.chapter_number, Chapter.title)
                        .where(Chapter.id == mem['metadata'].get('chapter_id'))
                    )
                    chapter_info = chapter_result.first()
                    if chapter_info:
                        relevant_chapters_text.append(
                            f"第{chapter_info.chapter_number}章《{chapter_info.title}》：{mem['content']} "
                            f"(相关度:{mem['similarity']:.2f})"
                        )
                
                context_parts['relevant_history'] = "【相关历史章节】\n" + "\n".join(relevant_chapters_text)
                logger.info(f"  ✅ 相关历史：语义检索到{len(relevant_chapters_text)}章")
        
        # 4. 近期概要（最近30章，每章200字摘要）
        recent_summary_count = min(30, total_previous)
        recent_for_summary = all_chapters_info[-recent_summary_count:] if total_previous > 3 else []
        
        if recent_for_summary and len(recent_for_summary) > 3:  # 至少要有3章才做摘要
            recent_summaries = []
            for chapter_info in recent_for_summary[:-3]:  # 排除最后3章（它们会完整展示）
                # 优先获取chapter_summary记忆
                summary_result = await db.execute(
                    select(StoryMemory.content)
                    .where(StoryMemory.project_id == project_id)
                    .where(StoryMemory.chapter_id == chapter_info.id)
                    .where(StoryMemory.memory_type == 'chapter_summary')
                    .limit(1)
                )
                summary = summary_result.scalar_one_or_none()
                
                if summary:
                    recent_summaries.append(
                        f"第{chapter_info.chapter_number}章《{chapter_info.title}》：{summary}"
                    )
            
            if recent_summaries:
                context_parts['recent_summary'] = "【近期章节概要】\n" + "\n".join(recent_summaries)
                logger.info(f"  ✅ 近期概要：{len(recent_summaries)}章摘要")
        
        # 5. 最近完整内容（最近3章）
        recent_full_count = min(3, total_previous)
        recent_full_chapters = all_chapters_info[-recent_full_count:]
        
        # 获取完整内容
        recent_full_texts = []
        for chapter_info in recent_full_chapters:
            chapter_result = await db.execute(
                select(Chapter.content)
                .where(Chapter.id == chapter_info.id)
            )
            content = chapter_result.scalar_one_or_none()
            if content:
                recent_full_texts.append(
                    f"=== 第{chapter_info.chapter_number}章：{chapter_info.title} ===\n{content}"
                )
        
        context_parts['recent_full'] = "【最近章节完整内容】\n" + "\n\n".join(recent_full_texts)
        logger.info(f"  ✅ 最近完整：{len(recent_full_texts)}章全文")
        
        # 6. 统计信息
        context_parts['stats'] = {
            'total_previous': total_previous,
            'skeleton_samples': len(skeleton_chapters),
            'relevant_history': len(relevant_memories) if current_outline and total_previous > 3 else 0,
            'recent_summaries': len(recent_summaries) if recent_for_summary and len(recent_for_summary) > 3 else 0,
            'recent_full': len(recent_full_texts)
        }
        
        # 计算总长度
        total_length = sum([
            len(context_parts['story_skeleton']),
            len(context_parts['relevant_history']),
            len(context_parts['recent_summary']),
            len(context_parts['recent_full'])
        ])
        context_parts['stats']['total_length'] = total_length
        
        logger.info(f"📊 智能上下文构建完成：总长度 {total_length} 字符")
        
    except Exception as e:
        logger.error(f"❌ 构建智能上下文失败: {str(e)}", exc_info=True)
    
    return context_parts


@router.get("/{chapter_id}/can-generate", summary="检查章节是否可以生成")
async def check_can_generate(
    chapter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    检查章节是否满足生成条件
    返回可生成状态和前置章节信息
    """
    # 获取章节
    result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    # 验证用户权限
    user_id = getattr(request.state, 'user_id', None)
    await verify_project_access(chapter.project_id, user_id, db)
    
    # 检查前置条件
    can_generate, error_msg, previous_chapters = await check_prerequisites(db, chapter)
    
    # 构建前置章节信息
    previous_info = [
        {
            "id": ch.id,
            "chapter_number": ch.chapter_number,
            "title": ch.title,
            "has_content": bool(ch.content and ch.content.strip()),
            "word_count": ch.word_count or 0
        }
        for ch in previous_chapters
    ]
    
    return {
        "can_generate": can_generate,
        "reason": error_msg if not can_generate else "",
        "previous_chapters": previous_info,
        "chapter_number": chapter.chapter_number
    }


async def analyze_chapter_background(
    chapter_id: str,
    user_id: str,
    project_id: str,
    task_id: str,
    ai_service: AIService
):
    """
    后台异步分析章节（支持并发，使用锁保护数据库写入）
    
    参数：
        chapter_id: 章节ID
        user_id: 用户ID
        project_id: 项目ID
        task_id: 任务ID
        ai_service: AI服务实例
    """
    db_session = None
    write_lock = await get_db_write_lock(user_id)
    
    try:
        logger.info(f"🔍 开始分析章节: {chapter_id}, 任务ID: {task_id}")
        
        # 创建独立数据库会话
        from app.database import get_engine
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        
        engine = await get_engine(user_id)
        AsyncSessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        db_session = AsyncSessionLocal()
        
        # 1. 获取任务（读操作）
        task_result = await db_session.execute(
            select(AnalysisTask).where(AnalysisTask.id == task_id)
        )
        task = task_result.scalar_one_or_none()
        
        if not task:
            logger.error(f"❌ 任务不存在: {task_id}")
            return
        
        # 更新任务状态（写操作，需要锁）
        async with write_lock:
            task.status = 'running'
            task.started_at = datetime.now()
            task.progress = 10
            await db_session.commit()
        
        # 2. 获取章节信息（读操作）
        chapter_result = await db_session.execute(
            select(Chapter).where(Chapter.id == chapter_id)
        )
        chapter = chapter_result.scalar_one_or_none()
        if not chapter or not chapter.content:
            async with write_lock:
                task.status = 'failed'
                task.error_message = '章节不存在或内容为空'
                task.completed_at = datetime.now()
                await db_session.commit()
            logger.error(f"❌ 章节不存在或内容为空: {chapter_id}")
            return
        
        async with write_lock:
            task.progress = 20
            await db_session.commit()
        
        # 3. 使用PlotAnalyzer分析章节
        analyzer = PlotAnalyzer(ai_service)
        analysis_result = await analyzer.analyze_chapter(
            chapter_number=chapter.chapter_number,
            title=chapter.title,
            content=chapter.content,
            word_count=chapter.word_count or len(chapter.content)
        )
        
        if not analysis_result:
            async with write_lock:
                task.status = 'failed'
                task.error_message = 'AI分析失败，请检查日志'
                task.completed_at = datetime.now()
                await db_session.commit()
            logger.error(f"❌ AI分析失败: {chapter_id}")
            return
        
        async with write_lock:
            task.progress = 60
            await db_session.commit()
        
        # 4. 保存分析结果到数据库（写操作，需要锁）
        async with write_lock:
            existing_analysis_result = await db_session.execute(
                select(PlotAnalysis).where(PlotAnalysis.chapter_id == chapter_id)
            )
            existing_analysis = existing_analysis_result.scalar_one_or_none()
            
            if existing_analysis:
                # 更新现有记录
                logger.info(f"  更新现有分析记录: {existing_analysis.id}")
                existing_analysis.plot_stage = analysis_result.get('plot_stage', '发展')
                existing_analysis.conflict_level = analysis_result.get('conflict', {}).get('level', 0)
                existing_analysis.conflict_types = analysis_result.get('conflict', {}).get('types', [])
                existing_analysis.emotional_tone = analysis_result.get('emotional_arc', {}).get('primary_emotion', '')
                existing_analysis.emotional_intensity = analysis_result.get('emotional_arc', {}).get('intensity', 0) / 10.0
                existing_analysis.hooks = analysis_result.get('hooks', [])
                existing_analysis.hooks_count = len(analysis_result.get('hooks', []))
                existing_analysis.foreshadows = analysis_result.get('foreshadows', [])
                existing_analysis.foreshadows_planted = sum(1 for f in analysis_result.get('foreshadows', []) if f.get('type') == 'planted')
                existing_analysis.foreshadows_resolved = sum(1 for f in analysis_result.get('foreshadows', []) if f.get('type') == 'resolved')
                existing_analysis.plot_points = analysis_result.get('plot_points', [])
                existing_analysis.plot_points_count = len(analysis_result.get('plot_points', []))
                existing_analysis.character_states = analysis_result.get('character_states', [])
                existing_analysis.scenes = analysis_result.get('scenes', [])
                existing_analysis.pacing = analysis_result.get('pacing', 'moderate')
                existing_analysis.overall_quality_score = analysis_result.get('scores', {}).get('overall', 0)
                existing_analysis.pacing_score = analysis_result.get('scores', {}).get('pacing', 0)
                existing_analysis.engagement_score = analysis_result.get('scores', {}).get('engagement', 0)
                existing_analysis.coherence_score = analysis_result.get('scores', {}).get('coherence', 0)
                existing_analysis.analysis_report = analyzer.generate_analysis_summary(analysis_result)
                existing_analysis.suggestions = analysis_result.get('suggestions', [])
                existing_analysis.dialogue_ratio = analysis_result.get('dialogue_ratio', 0)
                existing_analysis.description_ratio = analysis_result.get('description_ratio', 0)
            else:
                # 创建新记录
                logger.info(f"  创建新的分析记录")
                plot_analysis = PlotAnalysis(
                    chapter_id=chapter_id,
                    project_id=project_id,
                    plot_stage=analysis_result.get('plot_stage', '发展'),
                    conflict_level=analysis_result.get('conflict', {}).get('level', 0),
                    conflict_types=analysis_result.get('conflict', {}).get('types', []),
                    emotional_tone=analysis_result.get('emotional_arc', {}).get('primary_emotion', ''),
                    emotional_intensity=analysis_result.get('emotional_arc', {}).get('intensity', 0) / 10.0,
                    hooks=analysis_result.get('hooks', []),
                    hooks_count=len(analysis_result.get('hooks', [])),
                    foreshadows=analysis_result.get('foreshadows', []),
                    foreshadows_planted=sum(1 for f in analysis_result.get('foreshadows', []) if f.get('type') == 'planted'),
                    foreshadows_resolved=sum(1 for f in analysis_result.get('foreshadows', []) if f.get('type') == 'resolved'),
                    plot_points=analysis_result.get('plot_points', []),
                    plot_points_count=len(analysis_result.get('plot_points', [])),
                    character_states=analysis_result.get('character_states', []),
                    scenes=analysis_result.get('scenes', []),
                    pacing=analysis_result.get('pacing', 'moderate'),
                    overall_quality_score=analysis_result.get('scores', {}).get('overall', 0),
                    pacing_score=analysis_result.get('scores', {}).get('pacing', 0),
                    engagement_score=analysis_result.get('scores', {}).get('engagement', 0),
                    coherence_score=analysis_result.get('scores', {}).get('coherence', 0),
                    analysis_report=analyzer.generate_analysis_summary(analysis_result),
                    suggestions=analysis_result.get('suggestions', []),
                    dialogue_ratio=analysis_result.get('dialogue_ratio', 0),
                    description_ratio=analysis_result.get('description_ratio', 0)
                )
                db_session.add(plot_analysis)
            
            await db_session.commit()
            
            task.progress = 80
            await db_session.commit()
        
        # 5. 提取记忆并保存到向量数据库（传入章节内容用于计算位置）
        memories = analyzer.extract_memories_from_analysis(
            analysis=analysis_result,
            chapter_id=chapter_id,
            chapter_number=chapter.chapter_number,
            chapter_content=chapter.content or "",
            chapter_title=chapter.title or ""
        )
        
        # 先删除该章节的旧记忆（写操作，需要锁）
        async with write_lock:
            old_memories_result = await db_session.execute(
                select(StoryMemory).where(StoryMemory.chapter_id == chapter_id)
            )
            old_memories = old_memories_result.scalars().all()
            for old_mem in old_memories:
                await db_session.delete(old_mem)
            await db_session.commit()
            logger.info(f"  删除旧记忆: {len(old_memories)}条")
        
        # 准备批量添加的记忆数据（不需要锁）
        memory_records = []
        for mem in memories:
            memory_id = f"{chapter_id}_{mem['type']}_{len(memory_records)}"
            memory_records.append({
                'id': memory_id,
                'content': mem['content'],
                'type': mem['type'],
                'metadata': mem['metadata']
            })
            
        # 保存到关系数据库（写操作，需要锁）
        async with write_lock:
            for mem in memories:
                memory_id = memory_records[memories.index(mem)]['id']
                text_position = mem['metadata'].get('text_position', -1)
                text_length = mem['metadata'].get('text_length', 0)
                
                story_memory = StoryMemory(
                    id=memory_id,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    memory_type=mem['type'],
                    content=mem['content'],
                    title=mem['title'],
                    importance_score=mem['metadata'].get('importance_score', 0.5),
                    tags=mem['metadata'].get('tags', []),
                    is_foreshadow=mem['metadata'].get('is_foreshadow', 0),
                    story_timeline=chapter.chapter_number,
                    chapter_position=text_position,
                    text_length=text_length,
                    related_characters=mem['metadata'].get('related_characters', []),
                    related_locations=mem['metadata'].get('related_locations', [])
                )
                db_session.add(story_memory)
                
                if text_position >= 0:
                    logger.debug(f"  保存记忆 {memory_id}: position={text_position}, length={text_length}")
            
            await db_session.commit()
        
        # 批量添加到向量数据库
        if memory_records:
            added_count = await memory_service.batch_add_memories(
                user_id=user_id,
                project_id=project_id,
                memories=memory_records
            )
            logger.info(f"✅ 添加{added_count}条记忆到向量库")
        
        # 最终更新任务状态（写操作，需要锁）- 增加重试机制
        update_success = False
        for retry in range(3):
            try:
                async with write_lock:
                    task.progress = 100
                    task.status = 'completed'
                    task.completed_at = datetime.now()
                    await db_session.commit()
                    update_success = True
                    logger.info(f"✅ 章节分析完成: {chapter_id}, 提取{len(memories)}条记忆")
                    break
            except Exception as commit_error:
                logger.error(f"❌ 提交任务完成状态失败(重试{retry+1}/3): {str(commit_error)}")
                if retry < 2:
                    await asyncio.sleep(0.1)
                else:
                    logger.error(f"❌ 无法更新任务为completed状态: {task_id}")
                    # 即使失败也不抛出异常，因为分析本身已经完成
        
        if not update_success:
            logger.warning(f"⚠️  章节分析完成但状态更新失败: {chapter_id}")
        
    except Exception as e:
        logger.error(f"❌ 后台分析异常: {str(e)}", exc_info=True)
        # 确保任务状态被更新为failed（写操作，需要锁）
        if db_session:
            # 多次重试更新任务状态
            for retry in range(3):
                try:
                    async with write_lock:
                        # 重新获取任务（可能是旧会话导致的问题）
                        task_result = await db_session.execute(
                            select(AnalysisTask).where(AnalysisTask.id == task_id)
                        )
                        task = task_result.scalar_one_or_none()
                        if task:
                            task.status = 'failed'
                            task.error_message = str(e)[:500]
                            task.completed_at = datetime.now()
                            task.progress = 0
                            await db_session.commit()
                            logger.info(f"✅ 任务状态已更新为failed: {task_id} (重试{retry+1}次)")
                            break
                        else:
                            logger.error(f"❌ 无法找到任务进行状态更新: {task_id}")
                            break
                except Exception as update_error:
                    logger.error(f"❌ 更新任务状态失败(重试{retry+1}/3): {str(update_error)}")
                    if retry < 2:
                        await asyncio.sleep(0.1)  # 短暂等待后重试
                    else:
                        logger.error(f"❌ 任务状态更新失败，已达到最大重试次数: {task_id}")
    finally:
        if db_session:
            await db_session.close()


@router.post("/{chapter_id}/generate-stream", summary="AI创作章节内容（流式）")
async def generate_chapter_content_stream(
    chapter_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    generate_request: ChapterGenerateRequest = ChapterGenerateRequest(),
    user_ai_service: AIService = Depends(get_user_ai_service)
):
    """
    根据大纲、前置章节内容和项目信息AI创作章节完整内容（流式返回）
    要求：必须按顺序生成，确保前置章节都已完成
    
    请求体参数：
    - style_id: 可选，指定使用的写作风格ID。不提供则不使用任何风格
    - target_word_count: 可选，目标字数，默认3000字，范围500-10000字
    - enable_mcp: 可选，是否启用MCP工具增强，默认True
    
    注意：此函数不使用依赖注入的db，而是在生成器内部创建独立的数据库会话
    以避免流式响应期间的连接泄漏问题
    """
    style_id = generate_request.style_id
    target_word_count = generate_request.target_word_count or 3000
    enable_mcp = generate_request.enable_mcp if hasattr(generate_request, 'enable_mcp') else True
    one_time_prompt = generate_request.one_time_prompt
    log_chapter_generation_step(
        logger,
        chapter_id=chapter_id,
        step=1,
        message="解析生成参数",
        style_id=style_id or "未指定",
        target_word_count=target_word_count,
        enable_mcp=enable_mcp,
        one_time_prompt_chars=len((one_time_prompt or "").strip()),
    )

    # 预先验证章节存在性（使用临时会话）
    async for temp_db in get_db(request):
        try:
            result = await temp_db.execute(
                select(Chapter).where(Chapter.id == chapter_id)
            )
            chapter = result.scalar_one_or_none()
            if not chapter:
                raise HTTPException(status_code=404, detail="章节不存在")
            
            # 检查前置条件
            can_generate, error_msg, previous_chapters = await check_prerequisites(temp_db, chapter)
            if not can_generate:
                raise HTTPException(status_code=400, detail=error_msg)
            
            # 保存前置章节数据供生成器使用
            previous_chapters_data = [
                {
                    'id': ch.id,
                    'chapter_number': ch.chapter_number,
                    'title': ch.title,
                    'content': ch.content
                }
                for ch in previous_chapters
            ]
            log_chapter_generation_step(
                logger,
                chapter_id=chapter_id,
                step=2,
                message="章节与前置条件校验通过",
                chapter_number=chapter.chapter_number,
                previous_chapters=len(previous_chapters_data),
            )
        finally:
            await temp_db.close()
        break
    
    async def event_generator():
        # 在生成器内部创建独立的数据库会话
        db_session = None
        db_committed = False
        # 获取当前用户ID（在生成器外部就需要）
        current_user_id = getattr(request.state, "user_id", "system")
        log_chapter_generation_step(
            logger,
            chapter_id=chapter_id,
            step=3,
            message="加载章节、项目、大纲、角色和写作风格",
            user_id=current_user_id,
        )
        
        try:
            # 创建新的数据库会话
            async for db_session in get_db(request):
                # 重新获取章节信息
                chapter_result = await db_session.execute(
                    select(Chapter).where(Chapter.id == chapter_id)
                )
                current_chapter = chapter_result.scalar_one_or_none()
                if not current_chapter:
                    yield f"data: {json.dumps({'type': 'error', 'error': '章节不存在'}, ensure_ascii=False)}\n\n"
                    return
            
                # 获取项目信息
                project_result = await db_session.execute(
                    select(Project).where(Project.id == current_chapter.project_id)
                )
                project = project_result.scalar_one_or_none()
                if not project:
                    yield f"data: {json.dumps({'type': 'error', 'error': '项目不存在'}, ensure_ascii=False)}\n\n"
                    return
                
                # 获取对应的大纲
                outline_result = await db_session.execute(
                    select(Outline)
                    .where(Outline.project_id == current_chapter.project_id)
                    .where(Outline.order_index == current_chapter.chapter_number)
                    .execution_options(populate_existing=True)
                )
                outline = outline_result.scalar_one_or_none()
                
                # 获取所有大纲用于上下文
                all_outlines_result = await db_session.execute(
                    select(Outline)
                    .where(Outline.project_id == current_chapter.project_id)
                    .order_by(Outline.order_index)
                    .execution_options(populate_existing=True)
                )
                all_outlines = all_outlines_result.scalars().all()
                outlines_context = "\n".join([
                    f"第{o.order_index}章 {o.title}: {o.content[:100]}..."
                    for o in all_outlines
                ])
                
                # 获取角色信息
                characters_result = await db_session.execute(
                    select(Character).where(Character.project_id == current_chapter.project_id)
                )
                characters = characters_result.scalars().all()
                characters_info = "\n".join([
                    f"- {c.name}({'组织' if c.is_organization else '角色'}, {c.role_type}): {c.personality[:100] if c.personality else ''}"
                    for c in characters
                ])
                
                # 获取写作风格
                style_content = ""
                if style_id:
                    # 使用指定的风格
                    style_result = await db_session.execute(
                        select(WritingStyle).where(WritingStyle.id == style_id)
                    )
                    style = style_result.scalar_one_or_none()
                    if style:
                        # 验证风格是否可用：全局预设风格（project_id为NULL）或者当前项目的自定义风格
                        if style.project_id is None or style.project_id == current_chapter.project_id:
                            style_content = style.prompt_content or ""
                            style_type = "全局预设" if style.project_id is None else "项目自定义"
                            logger.info(f"使用指定风格: {style.name} ({style_type})")
                        else:
                            logger.warning(f"风格 {style_id} 不属于当前项目，无法使用")
                    else:
                        logger.warning(f"未找到风格 {style_id}")
                else:
                    logger.info("未指定写作风格，使用原始提示词")

                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=3,
                    message="基础资料加载完成",
                    outlines=len(all_outlines),
                    characters=len(characters),
                    style_chars=len(style_content),
                )
                
                # 🚀 使用智能上下文构建（支持海量章节）
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=4,
                    message="构建前置章节智能上下文",
                )
                smart_context = await build_smart_chapter_context(
                    db=db_session,
                    project_id=project.id,
                    current_chapter_number=current_chapter.chapter_number,
                    user_id=current_user_id
                )
                
                # 组装上下文
                previous_content = ""
                if smart_context['story_skeleton']:
                    previous_content += smart_context['story_skeleton'] + "\n\n"
                if smart_context['relevant_history']:
                    previous_content += smart_context['relevant_history'] + "\n\n"
                if smart_context['recent_summary']:
                    previous_content += smart_context['recent_summary'] + "\n\n"
                if smart_context['recent_full']:
                    previous_content += smart_context['recent_full']
                
                # 日志输出统计信息
                stats = smart_context['stats']
                logger.info(f"📊 智能上下文统计:")
                logger.info(f"  - 前置章节总数: {stats.get('total_previous', 0)}")
                logger.info(f"  - 故事骨架采样: {stats.get('skeleton_samples', 0)}章")
                logger.info(f"  - 相关历史检索: {stats.get('relevant_history', 0)}章")
                logger.info(f"  - 近期章节概要: {stats.get('recent_summaries', 0)}章")
                logger.info(f"  - 最近完整内容: {stats.get('recent_full', 0)}章")
                logger.info(f"  - 上下文总长度: {stats.get('total_length', 0)}字符")
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=4,
                    message="前置章节智能上下文构建完成",
                    context_chars=stats.get('total_length', 0),
                )
                
                # 🧠 构建记忆增强上下文
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=5,
                    message="构建长期记忆增强上下文",
                )
                memory_context = await memory_service.build_context_for_generation(
                    user_id=current_user_id,
                    project_id=project.id,
                    current_chapter=current_chapter.chapter_number,
                    chapter_outline=outline.content if outline else current_chapter.summary or "",
                    character_names=[c.name for c in characters] if characters else None
                )
                
                # 计算各部分的字符长度
                context_lengths = {
                    'recent_context': len(memory_context.get('recent_context', '')),
                    'relevant_memories': len(memory_context.get('relevant_memories', '')),
                    'foreshadows': len(memory_context.get('foreshadows', '')),
                    'character_states': len(memory_context.get('character_states', '')),
                    'plot_points': len(memory_context.get('plot_points', ''))
                }
                total_memory_length = sum(context_lengths.values())
                
                logger.info(f"✅ 记忆上下文构建完成: {memory_context['stats']}")
                logger.info(f"📏 记忆上下文长度统计:")
                logger.info(f"  - 最近章节记忆: {context_lengths['recent_context']} 字符")
                logger.info(f"  - 语义相关记忆: {context_lengths['relevant_memories']} 字符")
                logger.info(f"  - 未完结伏笔: {context_lengths['foreshadows']} 字符")
                logger.info(f"  - 角色状态记忆: {context_lengths['character_states']} 字符")
                logger.info(f"  - 重要情节点: {context_lengths['plot_points']} 字符")
                logger.info(f"  - 记忆总长度: {total_memory_length} 字符")
                logger.info(f"  - 前置章节上下文长度: {len(previous_content)} 字符")
                logger.info(f"  - 总上下文长度(估算): {total_memory_length + len(previous_content) + 2000} 字符")
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=5,
                    message="长期记忆增强上下文构建完成",
                    memory_chars=total_memory_length,
                )
            
                # 发送开始事件
                yield f"data: {json.dumps({'type': 'start', 'message': '开始AI创作...'}, ensure_ascii=False)}\n\n"
                
                # 🔧 MCP工具增强：收集章节参考资料
                mcp_reference_materials = ""
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=6,
                    message="执行 MCP 工具规划与资料收集",
                    enable_mcp=enable_mcp,
                )
                if enable_mcp and current_user_id:
                    try:
                        yield f"data: {json.dumps({'type': 'progress', 'message': '🔍 尝试使用MCP工具收集参考资料...', 'progress': 28}, ensure_ascii=False)}\n\n"
                        
                        # 构建资料收集提示词
                        planning_prompt = f"""你正在为小说《{project.title}》创作第{current_chapter.chapter_number}章《{current_chapter.title}》。

【章节大纲】
{outline.content if outline else current_chapter.summary or '暂无大纲'}

【小说信息】
- 题材：{project.genre or '未设定'}
- 主题：{project.theme or '未设定'}
- 时代背景：{project.world_time_period or '未设定'}
- 地理位置：{project.world_location or '未设定'}

【任务】
请使用可用工具搜索相关背景资料，帮助创作更真实、更有深度的章节内容。
你可以查询：
1. 该章节涉及的历史事件或时代背景
2. 地理环境和场景描写参考
3. 相关领域的专业知识（如武术、科技、魔法等）
4. 文化习俗和生活细节
5. 根据章节标签查询示例文章
6. 根据写作风格查询示例写作风格的文章

请根据章节内容，有针对性地查询1-2个最关键的问题。"""
                        
                        # 调用MCP增强的AI（非流式，最多2轮工具调用）
                        planning_result = await user_ai_service.generate_text_with_mcp(
                            prompt=planning_prompt,
                            user_id=current_user_id,
                            db_session=db_session,
                            enable_mcp=True,
                            max_tool_rounds=2,
                            tool_choice="auto",
                            provider=None,
                            model=None
                        )
                        
                        # 提取参考资料
                        tool_count = planning_result.get("tool_calls_made", 0)
                        if tool_count > 0:
                            yield f"data: {json.dumps({'type': 'progress', 'message': f'✅ MCP工具调用成功（{tool_count}次）', 'progress': 32}, ensure_ascii=False)}\n\n"
                            mcp_reference_materials = planning_result.get("content", "")
                            logger.info(f"📚 MCP工具收集参考资料：{len(mcp_reference_materials)} 字符")
                        else:
                            yield f"data: {json.dumps({'type': 'progress', 'message': 'ℹ️ 未使用MCP工具（无可用工具或不需要）', 'progress': 32}, ensure_ascii=False)}\n\n"

                        log_chapter_generation_step(
                            logger,
                            chapter_id=chapter_id,
                            step=6,
                            message="MCP 工具规划与资料收集完成",
                            tool_calls=tool_count,
                            reference_chars=len(mcp_reference_materials),
                        )
                            
                    except Exception as e:
                        logger.warning(f"MCP工具调用失败（降级处理）: {e}")
                        log_chapter_generation_step(
                            logger,
                            chapter_id=chapter_id,
                            step=6,
                            message="MCP 工具调用失败，已降级为基础模式",
                            error_type=type(e).__name__,
                        )
                        yield f"data: {json.dumps({'type': 'progress', 'message': '⚠️ MCP工具暂时不可用，使用基础模式', 'progress': 32}, ensure_ascii=False)}\n\n"
                else:
                    log_chapter_generation_step(
                        logger,
                        chapter_id=chapter_id,
                        step=6,
                        message="跳过 MCP 工具规划",
                        reason="未启用 MCP 或缺少用户标识",
                    )
                
                # 根据是否有前置内容选择不同的提示词，并应用写作风格、记忆增强和MCP参考资料
                chapter_outline_text = outline.content if outline else current_chapter.summary or '暂无大纲'
                corpus_context = ""
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=7,
                    message="调用语料库 MCP 获取范文与风格参考",
                    enable_mcp=enable_mcp,
                )
                if enable_mcp and current_user_id:
                    corpus_context = await CorpusBridge(
                        current_user_id,
                        db_session,
                    ).get_reference_for_chapter(
                        chapter_outline=chapter_outline_text,
                        genre=project.genre or '',
                        limit=5,
                    )
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=7,
                    message="语料库 MCP 参考准备完成",
                    corpus_context_chars=len(corpus_context),
                    status="完成" if enable_mcp and current_user_id else "跳过",
                )

                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=8,
                    message="组装章节最终 Prompt",
                    has_previous_content=bool(previous_content),
                )
                if previous_content:
                    prompt = prompt_service.get_chapter_generation_with_context_prompt(
                        title=project.title,
                        theme=project.theme or '',
                        genre=project.genre or '',
                        narrative_perspective=project.narrative_perspective or '第三人称',
                        time_period=project.world_time_period or '未设定',
                        location=project.world_location or '未设定',
                        atmosphere=project.world_atmosphere or '未设定',
                        rules=project.world_rules or '未设定',
                        characters_info=characters_info or '暂无角色信息',
                        outlines_context=outlines_context,
                        previous_content=previous_content,
                        chapter_number=current_chapter.chapter_number,
                        chapter_title=current_chapter.title,
                        chapter_outline=chapter_outline_text,
                        style_content=style_content,
                        target_word_count=target_word_count,
                        memory_context=memory_context,
                        mcp_references=mcp_reference_materials,
                        corpus_context=corpus_context
                    )
                else:
                    prompt = prompt_service.get_chapter_generation_prompt(
                        title=project.title,
                        theme=project.theme or '',
                        genre=project.genre or '',
                        narrative_perspective=project.narrative_perspective or '第三人称',
                        time_period=project.world_time_period or '未设定',
                        location=project.world_location or '未设定',
                        atmosphere=project.world_atmosphere or '未设定',
                        rules=project.world_rules or '未设定',
                        characters_info=characters_info or '暂无角色信息',
                        outlines_context=outlines_context,
                        chapter_number=current_chapter.chapter_number,
                        chapter_title=current_chapter.title,
                        chapter_outline=chapter_outline_text,
                        style_content=style_content,
                        target_word_count=target_word_count,
                        memory_context=memory_context,
                        mcp_references=mcp_reference_materials,
                        corpus_context=corpus_context
                    )

                prompt = append_one_time_prompt(prompt, one_time_prompt)
                
                if mcp_reference_materials:
                    logger.info(f"📖 已整合MCP参考资料（{len(mcp_reference_materials)}字符）到章节生成提示词")

                log_final_chapter_prompt(
                    logger,
                    mode="单章流式生成",
                    chapter_id=chapter_id,
                    chapter_number=current_chapter.chapter_number,
                    chapter_title=current_chapter.title,
                    prompt=prompt,
                )
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=8,
                    message="章节最终 Prompt 组装完成并已打印",
                    prompt_chars=len(prompt),
                )
                
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=9,
                    message="调用 AI 流式生成章节正文",
                    provider=getattr(user_ai_service, "api_provider", "未知"),
                    model=getattr(user_ai_service, "default_model", "未知"),
                )
                
                # 流式生成内容
                full_content = ""
                chunk_count = 0
                async for chunk in user_ai_service.generate_text_stream(prompt=prompt):
                    chunk_count += 1
                    full_content += chunk
                    yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0)  # 让出控制权
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=9,
                    message="AI 流式正文生成完成",
                    chunks=chunk_count,
                    generated_chars=len(full_content),
                )
                
                # 更新章节内容到数据库
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=10,
                    message="保存章节并创建后台分析任务",
                )
                old_word_count = current_chapter.word_count or 0
                current_chapter.content = full_content
                new_word_count = len(full_content)
                current_chapter.word_count = new_word_count
                current_chapter.status = "completed"
                
                # 更新项目字数
                project.current_words = project.current_words - old_word_count + new_word_count
                
                # 记录生成历史
                history = GenerationHistory(
                    project_id=current_chapter.project_id,
                    chapter_id=current_chapter.id,
                    prompt=f"创作章节: 第{current_chapter.chapter_number}章 {current_chapter.title}",
                    generated_content=full_content[:500] if len(full_content) > 500 else full_content,
                    model="default"
                )
                db_session.add(history)
                
                await db_session.commit()
                db_committed = True
                await db_session.refresh(current_chapter)
                
                logger.info(f"成功创作章节 {chapter_id}，共 {new_word_count} 字")
                
                # 创建分析任务
                analysis_task = AnalysisTask(
                    chapter_id=chapter_id,
                    user_id=current_user_id,
                    project_id=project.id,
                    status='pending',
                    progress=0
                )
                db_session.add(analysis_task)
                await db_session.commit()
                await db_session.refresh(analysis_task)
                
                task_id = analysis_task.id
                logger.info(f"📋 已创建分析任务: {task_id}")
                
                # 短暂延迟确保SQLite WAL完成写入
                await asyncio.sleep(0.05)
                
                # 直接启动后台分析（并发执行）
                background_tasks.add_task(
                    analyze_chapter_background,
                    chapter_id=chapter_id,
                    user_id=current_user_id,
                    project_id=project.id,
                    task_id=task_id,
                    ai_service=user_ai_service
                )
                log_chapter_generation_step(
                    logger,
                    chapter_id=chapter_id,
                    step=10,
                    message="章节保存完成，后台分析任务已启动",
                    word_count=new_word_count,
                    analysis_task_id=task_id,
                )
                
                # 发送完成事件（包含分析任务ID）
                completion_data = {
                    'type': 'done',
                    'message': '创作完成',
                    'word_count': new_word_count,
                    'analysis_task_id': task_id
                }
                yield f"data: {json.dumps(completion_data, ensure_ascii=False)}\n\n"
                
                # 发送分析开始事件
                analysis_started_data = {
                    'type': 'analysis_started',
                    'task_id': task_id,
                    'message': '章节分析已开始'
                }
                yield f"data: {json.dumps(analysis_started_data, ensure_ascii=False)}\n\n"
                
                break  # 退出async for db_session循环
        
        except GeneratorExit:
            # SSE连接断开
            logger.warning("章节生成器被提前关闭（SSE断开）")
            if db_session and not db_committed:
                try:
                    if db_session.in_transaction():
                        await db_session.rollback()
                        logger.info("章节生成事务已回滚（GeneratorExit）")
                except Exception as e:
                    logger.error(f"GeneratorExit回滚失败: {str(e)}")
        except Exception as e:
            logger.error(f"流式创作章节失败: {str(e)}")
            if db_session and not db_committed:
                try:
                    if db_session.in_transaction():
                        await db_session.rollback()
                        logger.info("章节生成事务已回滚（异常）")
                except Exception as rollback_error:
                    logger.error(f"回滚失败: {str(rollback_error)}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            # 确保数据库会话被正确关闭
            if db_session:
                try:
                    # 最后检查：确保没有未提交的事务
                    if not db_committed and db_session.in_transaction():
                        await db_session.rollback()
                        logger.warning("在finally中发现未提交事务，已回滚")
                    
                    await db_session.close()
                    logger.info("数据库会话已关闭")
                except Exception as close_error:
                    logger.error(f"关闭数据库会话失败: {str(close_error)}")
                    # 强制关闭
                    try:
                        await db_session.close()
                    except:
                        pass
    
    return create_sse_response(event_generator())


@router.get("/{chapter_id}/analysis/status", summary="查询章节分析任务状态")
async def get_analysis_task_status(
    chapter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    查询指定章节的最新分析任务状态
    
    自动恢复机制：
    - 如果任务状态为running且超过1分钟未更新，自动标记为failed
    - 如果任务状态为pending且超过2分钟未启动，自动标记为failed
    
    返回:
    - has_task: 是否存在分析任务
    - task_id: 任务ID（如果存在）
    - status: pending/running/completed/failed/none（如果不存在则为none）
    - progress: 0-100
    - error_message: 错误信息(如果失败)
    - auto_recovered: 是否被自动恢复
    - created_at: 创建时间
    - completed_at: 完成时间
    
    注意：当章节不存在或无权访问时返回404，当没有分析任务时返回has_task=false
    """
    from datetime import timedelta
    
    # 先获取章节以验证存在性和权限
    chapter_result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    chapter = chapter_result.scalar_one_or_none()
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    # 验证用户权限
    user_id = getattr(request.state, 'user_id', None)
    await verify_project_access(chapter.project_id, user_id, db)
    
    # 获取该章节最新的分析任务
    result = await db.execute(
        select(AnalysisTask)
        .where(AnalysisTask.chapter_id == chapter_id)
        .order_by(AnalysisTask.created_at.desc())
        .limit(1)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        # 返回无任务状态，而不是抛出404错误
        return {
            "has_task": False,
            "chapter_id": chapter_id,
            "status": "none",
            "progress": 0,
            "error_message": None,
            "auto_recovered": False,
            "task_id": None,
            "created_at": None,
            "started_at": None,
            "completed_at": None
        }
    
    auto_recovered = False
    current_time = datetime.now()
    
    # 自动恢复卡住的任务
    if task.status == 'running':
        # 如果任务在running状态超过1分钟，标记为失败
        if task.started_at and (current_time - task.started_at) > timedelta(minutes=1):
            task.status = 'failed'
            task.error_message = '任务超时（超过1分钟未完成，已自动恢复）'
            task.completed_at = current_time
            task.progress = 0
            auto_recovered = True
            await db.commit()
            await db.refresh(task)
            logger.warning(f"🔄 自动恢复卡住的任务: {task.id}, 章节: {chapter_id}")
    
    elif task.status == 'pending':
        # 如果任务在pending状态超过2分钟仍未开始，标记为失败
        if task.created_at and (current_time - task.created_at) > timedelta(minutes=2):
            task.status = 'failed'
            task.error_message = '任务启动超时（超过2分钟未启动，已自动恢复）'
            task.completed_at = current_time
            task.progress = 0
            auto_recovered = True
            await db.commit()
            await db.refresh(task)
            logger.warning(f"🔄 自动恢复未启动的任务: {task.id}, 章节: {chapter_id}")
    
    return {
        "has_task": True,
        "task_id": task.id,
        "chapter_id": task.chapter_id,
        "status": task.status,
        "progress": task.progress,
        "error_message": task.error_message,
        "auto_recovered": auto_recovered,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None
    }


@router.get("/{chapter_id}/analysis", summary="获取章节分析结果")
async def get_chapter_analysis(
    chapter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    获取章节的完整分析结果
    
    返回:
    - analysis_data: 完整的分析数据(JSON)
    - summary: 分析摘要文本
    - memories: 提取的记忆列表
    - created_at: 分析时间
    """
    # 先获取章节以验证权限
    chapter_result_check = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    chapter_check = chapter_result_check.scalar_one_or_none()
    if chapter_check:
        # 验证用户权限
        user_id = getattr(request.state, 'user_id', None)
        await verify_project_access(chapter_check.project_id, user_id, db)
    
    # 获取分析结果
    analysis_result = await db.execute(
        select(PlotAnalysis)
        .where(PlotAnalysis.chapter_id == chapter_id)
        .order_by(PlotAnalysis.created_at.desc())
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()
    
    if not analysis:
        raise HTTPException(status_code=404, detail="该章节暂无分析结果")
    
    # 获取相关记忆
    memories_result = await db.execute(
        select(StoryMemory)
        .where(StoryMemory.chapter_id == chapter_id)
        .order_by(StoryMemory.importance_score.desc())
    )
    memories = memories_result.scalars().all()
    
    return {
        "chapter_id": chapter_id,
        "analysis": analysis.to_dict(),  # 使用to_dict()方法
        "memories": [
            {
                "id": mem.id,
                "type": mem.memory_type,
                "title": mem.title,
                "content": mem.content,
                "importance": mem.importance_score,
                "tags": mem.tags,
                "is_foreshadow": mem.is_foreshadow,
                "position": mem.chapter_position,
                "related_characters": mem.related_characters
            }
            for mem in memories
        ],
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None
    }


@router.get("/{chapter_id}/annotations", summary="获取章节标注数据")
async def get_chapter_annotations(
    chapter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    获取章节的标注数据（用于前端展示标注）
    
    返回格式化的标注列表，包含精确位置信息
    适用于章节内容的可视化标注展示
    """
    # 验证用户权限
    user_id = getattr(request.state, 'user_id', None)
    
    # 获取章节
    chapter_result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    chapter = chapter_result.scalar_one_or_none()
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    # 验证项目访问权限
    await verify_project_access(chapter.project_id, user_id, db)
    
    # 获取分析结果
    analysis_result = await db.execute(
        select(PlotAnalysis)
        .where(PlotAnalysis.chapter_id == chapter_id)
        .order_by(PlotAnalysis.created_at.desc())
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()
    
    # 获取记忆
    memories_result = await db.execute(
        select(StoryMemory)
        .where(StoryMemory.chapter_id == chapter_id)
        .order_by(StoryMemory.importance_score.desc())
    )
    memories = memories_result.scalars().all()
    
    # 构建标注数据
    annotations = []
    
    for mem in memories:
        # 优先从数据库读取位置信息
        position = mem.chapter_position if mem.chapter_position is not None else -1
        length = mem.text_length if hasattr(mem, 'text_length') and mem.text_length is not None else 0
        metadata_extra = {}
        
        # 如果数据库中没有位置信息，尝试从分析数据中重新计算
        if position == -1 and analysis and chapter.content:
            # 根据记忆类型从分析数据中查找对应项
            if mem.memory_type == 'hook' and analysis.hooks:
                for hook in analysis.hooks:
                    # 通过标题或内容匹配
                    if mem.title and hook.get('type') in mem.title:
                        keyword = hook.get('keyword', '')
                        if keyword:
                            pos = chapter.content.find(keyword)
                            if pos != -1:
                                position = pos
                                length = len(keyword)
                        metadata_extra["strength"] = hook.get('strength', 5)
                        metadata_extra["position_desc"] = hook.get('position', '')
                        break
            
            elif mem.memory_type == 'foreshadow' and analysis.foreshadows:
                for foreshadow in analysis.foreshadows:
                    if foreshadow.get('content') in mem.content:
                        keyword = foreshadow.get('keyword', '')
                        if keyword:
                            pos = chapter.content.find(keyword)
                            if pos != -1:
                                position = pos
                                length = len(keyword)
                        metadata_extra["foreshadow_type"] = foreshadow.get('type', 'planted')
                        metadata_extra["strength"] = foreshadow.get('strength', 5)
                        break
            
            elif mem.memory_type == 'plot_point' and analysis.plot_points:
                for plot_point in analysis.plot_points:
                    if plot_point.get('content') in mem.content:
                        keyword = plot_point.get('keyword', '')
                        if keyword:
                            pos = chapter.content.find(keyword)
                            if pos != -1:
                                position = pos
                                length = len(keyword)
                        break
        else:
            # 如果数据库有位置，也从分析数据中提取额外的元数据
            if analysis:
                if mem.memory_type == 'hook' and analysis.hooks:
                    for hook in analysis.hooks:
                        if mem.title and hook.get('type') in mem.title:
                            metadata_extra["strength"] = hook.get('strength', 5)
                            metadata_extra["position_desc"] = hook.get('position', '')
                            break
                
                elif mem.memory_type == 'foreshadow' and analysis.foreshadows:
                    for foreshadow in analysis.foreshadows:
                        if foreshadow.get('content') in mem.content:
                            metadata_extra["foreshadow_type"] = foreshadow.get('type', 'planted')
                            metadata_extra["strength"] = foreshadow.get('strength', 5)
                            break
        
        annotation = {
            "id": mem.id,
            "type": mem.memory_type,
            "title": mem.title,
            "content": mem.content,
            "importance": mem.importance_score or 0.5,
            "position": position,
            "length": length,
            "tags": mem.tags or [],
            "metadata": {
                "is_foreshadow": mem.is_foreshadow,
                "related_characters": mem.related_characters or [],
                "related_locations": mem.related_locations or [],
                **metadata_extra
            }
        }
        
        annotations.append(annotation)
    
    return {
        "chapter_id": chapter_id,
        "chapter_number": chapter.chapter_number,
        "title": chapter.title,
        "word_count": chapter.word_count or 0,
        "annotations": annotations,
        "has_analysis": analysis is not None,
        "summary": {
            "total_annotations": len(annotations),
            "hooks": len([a for a in annotations if a["type"] == "hook"]),
            "foreshadows": len([a for a in annotations if a["type"] == "foreshadow"]),
            "plot_points": len([a for a in annotations if a["type"] == "plot_point"]),
            "character_events": len([a for a in annotations if a["type"] == "character_event"])
        }
    }


@router.post("/{chapter_id}/analyze", summary="手动触发章节分析")
async def trigger_chapter_analysis(
    chapter_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_ai_service: AIService = Depends(get_user_ai_service)
):
    """
    手动触发章节分析(用于重新分析或分析旧章节)
    """
    # 从请求中获取用户ID
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    # 验证章节存在
    chapter_result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    chapter = chapter_result.scalar_one_or_none()
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    if not chapter.content or chapter.content.strip() == "":
        raise HTTPException(status_code=400, detail="章节内容为空，无法分析")
    
    # 获取项目信息
    project_result = await db.execute(
        select(Project).where(Project.id == chapter.project_id)
    )
    project = project_result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 创建分析任务
    analysis_task = AnalysisTask(
        chapter_id=chapter_id,
        user_id=user_id,
        project_id=project.id,
        status='pending',
        progress=0
    )
    db.add(analysis_task)
    await db.commit()
    
    task_id = analysis_task.id
    logger.info(f"📋 创建分析任务: {task_id}, 章节: {chapter_id}")
    
    # 刷新数据库会话，确保其他会话可以看到新任务
    await db.refresh(analysis_task)
    
    # 短暂延迟确保SQLite WAL完成写入（让其他会话可见）
    await asyncio.sleep(3)
    
    # 直接启动后台分析（并发执行）
    background_tasks.add_task(
        analyze_chapter_background,
        chapter_id=chapter_id,
        user_id=user_id,
        project_id=project.id,
        task_id=task_id,
        ai_service=user_ai_service
    )
    
    return {
        "task_id": task_id,
        "chapter_id": chapter_id,
        "status": "pending",
        "message": "分析任务已创建并开始执行"
    }



def calculate_estimated_time(
    chapter_count: int,
    target_word_count: int,
    enable_analysis: bool
) -> int:
    """
    计算预估耗时（分钟）
    
    基准：
    - 生成3000字约需2分钟
    - 分析约需1分钟
    """
    generation_time_per_chapter = (target_word_count / 3000) * 2
    analysis_time_per_chapter = 1 if enable_analysis else 0
    
    total_time = chapter_count * (generation_time_per_chapter + analysis_time_per_chapter)
    
    return max(1, int(total_time))


@router.post("/project/{project_id}/batch-generate", response_model=BatchGenerateResponse, summary="批量顺序生成章节内容")
async def batch_generate_chapters_in_order(
    project_id: str,
    batch_request: BatchGenerateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_ai_service: AIService = Depends(get_user_ai_service)
):
    """
    从指定章节开始，按顺序批量生成指定数量的章节
    
    特性：
    1. 严格按章节序号顺序生成（不可跳过）
    2. 自动检测起始章节是否可生成
    3. 可选同步分析（影响耗时和质量）
    4. 失败后终止，不继续后续章节
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    # 验证项目存在和用户权限
    project = await verify_project_access(project_id, user_id, db)
    
    # 获取项目的所有章节，按序号排序
    result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )
    all_chapters = result.scalars().all()
    
    if not all_chapters:
        raise HTTPException(status_code=404, detail="项目没有章节")
    
    # 计算要生成的章节范围
    start_number = batch_request.start_chapter_number
    end_number = start_number + batch_request.count - 1
    
    # 筛选出要生成的章节
    chapters_to_generate = [
        ch for ch in all_chapters
        if start_number <= ch.chapter_number <= end_number
    ]
    
    if not chapters_to_generate:
        raise HTTPException(status_code=404, detail="指定范围内没有章节")
    
    # 验证起始章节的前置条件
    first_chapter = chapters_to_generate[0]
    can_generate, error_msg, _ = await check_prerequisites(db, first_chapter)
    if not can_generate:
        raise HTTPException(status_code=400, detail=f"起始章节无法生成：{error_msg}")
    
    # 创建批量生成任务
    batch_task = BatchGenerationTask(
        project_id=project_id,
        user_id=user_id,
        start_chapter_number=start_number,
        chapter_count=len(chapters_to_generate),
        chapter_ids=[ch.id for ch in chapters_to_generate],
        style_id=batch_request.style_id,
        target_word_count=batch_request.target_word_count,
        enable_analysis=batch_request.enable_analysis,
        max_retries=batch_request.max_retries,
        status='pending',
        total_chapters=len(chapters_to_generate),
        completed_chapters=0,
        failed_chapters=[],
        current_retry_count=0
    )
    db.add(batch_task)
    await db.commit()
    await db.refresh(batch_task)
    
    batch_id = batch_task.id
    
    # 计算预估耗时
    estimated_time = calculate_estimated_time(
        chapter_count=len(chapters_to_generate),
        target_word_count=batch_request.target_word_count,
        enable_analysis=batch_request.enable_analysis
    )
    
    logger.info(f"📦 创建批量生成任务: {batch_id}, 章节: 第{start_number}-{end_number}章, 预估耗时: {estimated_time}分钟")
    
    # 启动后台批量生成任务
    background_tasks.add_task(
        execute_batch_generation_in_order,
        batch_id=batch_id,
        user_id=user_id,
        ai_service=user_ai_service
    )
    
    return BatchGenerateResponse(
        batch_id=batch_id,
        message=f"批量生成任务已创建，将生成 {len(chapters_to_generate)} 个章节",
        chapters_to_generate=[
            {
                "id": ch.id,
                "chapter_number": ch.chapter_number,
                "title": ch.title
            }
            for ch in chapters_to_generate
        ],
        estimated_time_minutes=estimated_time
    )


@router.get("/batch-generate/{batch_id}/status", response_model=BatchGenerateStatusResponse, summary="查询批量生成任务状态")
async def get_batch_generation_status(
    batch_id: str,
    db: AsyncSession = Depends(get_db)
):
    """查询批量生成任务的状态和进度"""
    result = await db.execute(
        select(BatchGenerationTask).where(BatchGenerationTask.id == batch_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="批量生成任务不存在")
    
    return BatchGenerateStatusResponse(
        batch_id=task.id,
        status=task.status,
        total=task.total_chapters,
        completed=task.completed_chapters,
        current_chapter_id=task.current_chapter_id,
        current_chapter_number=task.current_chapter_number,
        current_retry_count=task.current_retry_count,
        max_retries=task.max_retries,
        failed_chapters=task.failed_chapters or [],
        created_at=task.created_at.isoformat() if task.created_at else None,
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        error_message=task.error_message
    )


@router.get("/project/{project_id}/batch-generate/active", summary="获取项目当前运行中的批量生成任务")
async def get_active_batch_generation(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    获取项目当前运行中的批量生成任务
    用于页面刷新后恢复任务状态
    """
    # 验证用户权限
    user_id = getattr(request.state, 'user_id', None)
    await verify_project_access(project_id, user_id, db)
    
    result = await db.execute(
        select(BatchGenerationTask)
        .where(BatchGenerationTask.project_id == project_id)
        .where(BatchGenerationTask.status.in_(['pending', 'running']))
        .order_by(BatchGenerationTask.created_at.desc())
        .limit(1)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        return {
            "has_active_task": False,
            "task": None
        }
    
    return {
        "has_active_task": True,
        "task": {
            "batch_id": task.id,
            "status": task.status,
            "total": task.total_chapters,
            "completed": task.completed_chapters,
            "current_chapter_id": task.current_chapter_id,
            "current_chapter_number": task.current_chapter_number,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None
        }
    }


@router.post("/batch-generate/{batch_id}/cancel", summary="取消批量生成任务")
async def cancel_batch_generation(
    batch_id: str,
    db: AsyncSession = Depends(get_db)
):
    """取消正在进行的批量生成任务"""
    result = await db.execute(
        select(BatchGenerationTask).where(BatchGenerationTask.id == batch_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="批量生成任务不存在")
    
    if task.status in ['completed', 'failed', 'cancelled']:
        raise HTTPException(status_code=400, detail=f"任务已处于 {task.status} 状态，无法取消")
    
    task.status = 'cancelled'
    task.completed_at = datetime.now()
    await db.commit()
    
    logger.info(f"🛑 批量生成任务已取消: {batch_id}")
    
    return {
        "message": "批量生成任务已取消",
        "batch_id": batch_id,
        "completed_chapters": task.completed_chapters,
        "total_chapters": task.total_chapters
    }


async def execute_batch_generation_in_order(
    batch_id: str,
    user_id: str,
    ai_service: AIService
):
    """
    按顺序执行批量生成任务（后台任务）
    - 严格按章节序号顺序
    - 任一章节失败则终止后续生成
    - 可选同步分析
    """
    db_session = None
    write_lock = await get_db_write_lock(user_id)
    
    try:
        logger.info(f"📦 开始执行顺序批量生成任务: {batch_id}")
        
        # 创建独立数据库会话
        from app.database import get_engine
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        
        engine = await get_engine(user_id)
        AsyncSessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        db_session = AsyncSessionLocal()
        
        # 获取任务
        task_result = await db_session.execute(
            select(BatchGenerationTask).where(BatchGenerationTask.id == batch_id)
        )
        task = task_result.scalar_one_or_none()
        
        if not task:
            logger.error(f"❌ 批量生成任务不存在: {batch_id}")
            return
        
        # 更新任务状态为运行中
        async with write_lock:
            task.status = 'running'
            task.started_at = datetime.now()
            await db_session.commit()
        
        # 按顺序生成每个章节
        for idx, chapter_id in enumerate(task.chapter_ids, 1):
            # 检查任务是否被取消
            await db_session.refresh(task)
            if task.status == 'cancelled':
                logger.info(f"🛑 批量生成任务已被取消: {batch_id}")
                return
            
            # 更新当前章节
            async with write_lock:
                task.current_chapter_id = chapter_id
                task.current_retry_count = 0  # 重置重试计数
                await db_session.commit()
            
            # 重试循环
            retry_count = 0
            chapter_success = False
            chapter = None
            last_error = None
            
            while retry_count <= task.max_retries and not chapter_success:
                try:
                    # 获取章节信息
                    chapter_result = await db_session.execute(
                        select(Chapter).where(Chapter.id == chapter_id)
                    )
                    chapter = chapter_result.scalar_one_or_none()
                    
                    if not chapter:
                        raise Exception(f"章节 {chapter_id} 不存在")
                    
                    # 更新当前章节序号和重试次数
                    async with write_lock:
                        task.current_chapter_number = chapter.chapter_number
                        task.current_retry_count = retry_count
                        await db_session.commit()
                    
                    if retry_count > 0:
                        logger.info(f"🔄 [{idx}/{task.total_chapters}] 重试生成章节 (第{retry_count}次): 第{chapter.chapter_number}章 《{chapter.title}》")
                    else:
                        logger.info(f"📝 [{idx}/{task.total_chapters}] 开始生成章节: 第{chapter.chapter_number}章 《{chapter.title}》")
                    
                    # 检查前置条件（每次都检查，确保顺序性）
                    can_generate, error_msg, _ = await check_prerequisites(db_session, chapter)
                    if not can_generate:
                        raise Exception(f"前置条件不满足: {error_msg}")
                    
                    # 生成章节内容（复用现有流式生成逻辑的核心部分）
                    await generate_single_chapter_for_batch(
                        db_session=db_session,
                        chapter=chapter,
                        user_id=user_id,
                        style_id=task.style_id,
                        target_word_count=task.target_word_count,
                        ai_service=ai_service,
                        write_lock=write_lock
                    )
                    
                    logger.info(f"✅ 章节生成完成: 第{chapter.chapter_number}章")
                    
                    # 如果启用同步分析
                    if task.enable_analysis:
                        logger.info(f"🔍 开始同步分析章节: 第{chapter.chapter_number}章")
                        
                        async with write_lock:
                            analysis_task = AnalysisTask(
                                chapter_id=chapter_id,
                                user_id=user_id,
                                project_id=task.project_id,
                                status='pending',
                                progress=0
                            )
                            db_session.add(analysis_task)
                            await db_session.commit()
                            await db_session.refresh(analysis_task)
                        
                        # 同步执行分析（等待完成）
                        await analyze_chapter_background(
                            chapter_id=chapter_id,
                            user_id=user_id,
                            project_id=task.project_id,
                            task_id=analysis_task.id,
                            ai_service=ai_service
                        )
                        
                        logger.info(f"✅ 章节分析完成: 第{chapter.chapter_number}章")
                    
                    # 标记成功
                    chapter_success = True
                    
                    # 更新完成数
                    async with write_lock:
                        task.completed_chapters += 1
                        task.current_retry_count = 0  # 重置重试计数
                        await db_session.commit()
                    
                    logger.info(f"✅ 进度: {task.completed_chapters}/{task.total_chapters}")
                    
                except Exception as e:
                    last_error = str(e)
                    logger.error(f"❌ 章节生成失败: 第{chapter.chapter_number if chapter else '?'}章, 错误: {last_error}")
                    
                    retry_count += 1
                    
                    # 如果还有重试机会，等待一小段时间后重试
                    if retry_count <= task.max_retries:
                        wait_time = min(2 ** retry_count, 10)  # 指数退避，最多等待10秒
                        logger.info(f"⏳ 等待 {wait_time} 秒后重试...")
                        await asyncio.sleep(wait_time)
                    else:
                        # 达到最大重试次数，记录失败信息
                        logger.error(f"❌ 章节生成失败，已达最大重试次数({task.max_retries}): 第{chapter.chapter_number if chapter else '?'}章")
                        
                        failed_info = {
                            'chapter_id': chapter_id,
                            'chapter_number': chapter.chapter_number if chapter else -1,
                            'title': chapter.title if chapter else '未知',
                            'error': last_error,
                            'retry_count': retry_count - 1
                        }
                        
                        async with write_lock:
                            if task.failed_chapters is None:
                                task.failed_chapters = []
                            task.failed_chapters.append(failed_info)
                            
                            # 标记任务失败并终止
                            task.status = 'failed'
                            task.error_message = f"第{chapter.chapter_number}章生成失败(重试{retry_count-1}次): {last_error}"[:500]
                            task.completed_at = datetime.now()
                            task.current_retry_count = 0
                            await db_session.commit()
                        
                        logger.error(f"🛑 批量生成终止于第{chapter.chapter_number}章")
                        return
        
        # 全部完成
        async with write_lock:
            task.status = 'completed'
            task.completed_at = datetime.now()
            task.current_chapter_id = None
            task.current_chapter_number = None
            await db_session.commit()
        
        logger.info(f"✅ 批量生成任务全部完成: {batch_id}, 成功生成 {task.completed_chapters} 章")
        
    except Exception as e:
        logger.error(f"❌ 批量生成任务异常: {str(e)}", exc_info=True)
        if db_session and task:
            try:
                async with write_lock:
                    task.status = 'failed'
                    task.error_message = str(e)[:500]
                    task.completed_at = datetime.now()
                    await db_session.commit()
            except Exception as commit_error:
                logger.error(f"❌ 更新任务失败状态失败: {str(commit_error)}")
    finally:
        if db_session:
            await db_session.close()


async def generate_single_chapter_for_batch(
    db_session: AsyncSession,
    chapter: Chapter,
    user_id: str,
    style_id: Optional[int],
    target_word_count: int,
    ai_service: AIService,
    write_lock: Lock
):
    """
    为批量生成执行单个章节的生成（非流式）
    复用现有生成逻辑的核心部分
    """
    # 获取项目信息
    project_result = await db_session.execute(
        select(Project).where(Project.id == chapter.project_id)
    )
    project = project_result.scalar_one_or_none()
    if not project:
        raise Exception("项目不存在")
    
    # 获取对应的大纲
    outline_result = await db_session.execute(
        select(Outline)
        .where(Outline.project_id == chapter.project_id)
        .where(Outline.order_index == chapter.chapter_number)
    )
    outline = outline_result.scalar_one_or_none()
    
    # 获取所有大纲用于上下文
    all_outlines_result = await db_session.execute(
        select(Outline)
        .where(Outline.project_id == chapter.project_id)
        .order_by(Outline.order_index)
    )
    all_outlines = all_outlines_result.scalars().all()
    outlines_context = "\n".join([
        f"第{o.order_index}章 {o.title}: {o.content[:100]}..."
        for o in all_outlines
    ])
    
    # 获取角色信息
    characters_result = await db_session.execute(
        select(Character).where(Character.project_id == chapter.project_id)
    )
    characters = characters_result.scalars().all()
    characters_info = "\n".join([
        f"- {c.name}({'组织' if c.is_organization else '角色'}, {c.role_type}): {c.personality[:100] if c.personality else ''}"
        for c in characters
    ])
    
    # 获取写作风格
    style_content = ""
    if style_id:
        style_result = await db_session.execute(
            select(WritingStyle).where(WritingStyle.id == style_id)
        )
        style = style_result.scalar_one_or_none()
        if style:
            if style.project_id is None or style.project_id == chapter.project_id:
                style_content = style.prompt_content or ""
    
    # 构建智能上下文
    smart_context = await build_smart_chapter_context(
        db=db_session,
        project_id=project.id,
        current_chapter_number=chapter.chapter_number,
        user_id=user_id
    )
    
    # 组装上下文
    previous_content = ""
    if smart_context['story_skeleton']:
        previous_content += smart_context['story_skeleton'] + "\n\n"
    if smart_context['relevant_history']:
        previous_content += smart_context['relevant_history'] + "\n\n"
    if smart_context['recent_summary']:
        previous_content += smart_context['recent_summary'] + "\n\n"
    if smart_context['recent_full']:
        previous_content += smart_context['recent_full']
    
    # 构建记忆增强上下文
    memory_context = await memory_service.build_context_for_generation(
        user_id=user_id,
        project_id=project.id,
        current_chapter=chapter.chapter_number,
        chapter_outline=outline.content if outline else chapter.summary or "",
        character_names=[c.name for c in characters] if characters else None
    )
    
    # 生成提示词
    chapter_outline_text = outline.content if outline else chapter.summary or '暂无大纲'
    corpus_context = await CorpusBridge(user_id, db_session).get_reference_for_chapter(
        chapter_outline=chapter_outline_text,
        genre=project.genre or '',
        limit=5,
    )
    if previous_content:
        prompt = prompt_service.get_chapter_generation_with_context_prompt(
            title=project.title,
            theme=project.theme or '',
            genre=project.genre or '',
            narrative_perspective=project.narrative_perspective or '第三人称',
            time_period=project.world_time_period or '未设定',
            location=project.world_location or '未设定',
            atmosphere=project.world_atmosphere or '未设定',
            rules=project.world_rules or '未设定',
            characters_info=characters_info or '暂无角色信息',
            outlines_context=outlines_context,
            previous_content=previous_content,
            chapter_number=chapter.chapter_number,
            chapter_title=chapter.title,
            chapter_outline=chapter_outline_text,
            style_content=style_content,
            target_word_count=target_word_count,
            memory_context=memory_context,
            corpus_context=corpus_context
        )
    else:
        prompt = prompt_service.get_chapter_generation_prompt(
            title=project.title,
            theme=project.theme or '',
            genre=project.genre or '',
            narrative_perspective=project.narrative_perspective or '第三人称',
            time_period=project.world_time_period or '未设定',
            location=project.world_location or '未设定',
            atmosphere=project.world_atmosphere or '未设定',
            rules=project.world_rules or '未设定',
            characters_info=characters_info or '暂无角色信息',
            outlines_context=outlines_context,
            chapter_number=chapter.chapter_number,
            chapter_title=chapter.title,
            chapter_outline=chapter_outline_text,
            style_content=style_content,
            target_word_count=target_word_count,
            memory_context=memory_context,
            corpus_context=corpus_context
        )

    log_final_chapter_prompt(
        logger,
        mode="批量生成",
        chapter_id=chapter.id,
        chapter_number=chapter.chapter_number,
        chapter_title=chapter.title,
        prompt=prompt,
    )
    
    # 非流式生成内容
    full_content = ""
    async for chunk in ai_service.generate_text_stream(prompt=prompt):
        full_content += chunk
    
    # 更新章节内容到数据库（使用锁保护）
    async with write_lock:
        old_word_count = chapter.word_count or 0
        chapter.content = full_content
        new_word_count = len(full_content)
        chapter.word_count = new_word_count
        chapter.status = "completed"
        
        # 更新项目字数
        project.current_words = project.current_words - old_word_count + new_word_count
        
        # 记录生成历史
        history = GenerationHistory(
            project_id=chapter.project_id,
            chapter_id=chapter.id,
            prompt=f"批量生成: 第{chapter.chapter_number}章 {chapter.title}",
            generated_content=full_content[:500] if len(full_content) > 500 else full_content,
            model="default"
        )
        db_session.add(history)
        
        await db_session.commit()
        await db_session.refresh(chapter)
    
    logger.info(f"✅ 单章节生成完成: 第{chapter.chapter_number}章，共 {new_word_count} 字")




# ==================== 章节重新生成相关API ====================

@router.post("/{chapter_id}/regenerate-stream", summary="流式重新生成章节内容")
async def regenerate_chapter_stream(
    chapter_id: str,
    request: Request,
    regenerate_request: ChapterRegenerateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_ai_service: AIService = Depends(get_user_ai_service)
):
    """
    根据分析建议或自定义指令重新生成章节内容（流式返回）
    
    工作流程：
    1. 验证章节和分析结果
    2. 创建重新生成任务
    3. 构建修改指令
    4. 流式生成新内容
    5. 保存为版本历史
    6. 可选自动应用
    """
    user_id = getattr(request.state, 'user_id', None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    # 验证章节存在
    chapter_result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    chapter = chapter_result.scalar_one_or_none()
    
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    if not chapter.content or chapter.content.strip() == "":
        raise HTTPException(status_code=400, detail="章节内容为空，无法重新生成")
    
    # 验证用户权限
    await verify_project_access(chapter.project_id, user_id, db)
    
    # 获取分析结果（如果使用分析建议）
    analysis = None
    if regenerate_request.modification_source in ['analysis_suggestions', 'mixed']:
        analysis_result = await db.execute(
            select(PlotAnalysis)
            .where(PlotAnalysis.chapter_id == chapter_id)
            .order_by(PlotAnalysis.created_at.desc())
            .limit(1)
        )
        analysis = analysis_result.scalar_one_or_none()
        
        if not analysis:
            raise HTTPException(status_code=404, detail="该章节暂无分析结果")
    
    # 预先获取项目上下文数据
    async for temp_db in get_db(request):
        try:
            # 获取项目信息
            project_result = await temp_db.execute(
                select(Project).where(Project.id == chapter.project_id)
            )
            project = project_result.scalar_one_or_none()
            
            # 获取角色信息
            characters_result = await temp_db.execute(
                select(Character).where(Character.project_id == chapter.project_id)
            )
            characters = characters_result.scalars().all()
            
            # 获取章节大纲
            outline_result = await temp_db.execute(
                select(Outline)
                .where(Outline.project_id == chapter.project_id)
                .where(Outline.order_index == chapter.chapter_number)
            )
            outline = outline_result.scalar_one_or_none()
            
            # 构建项目上下文
            project_context = {
                'project_title': project.title if project else '未知',
                'genre': project.genre if project else '未设定',
                'theme': project.theme if project else '未设定',
                'narrative_perspective': project.narrative_perspective if project else '第三人称',
                'time_period': project.world_time_period if project else '未设定',
                'location': project.world_location if project else '未设定',
                'atmosphere': project.world_atmosphere if project else '未设定',
                'characters_info': "\n".join([
                    f"- {c.name}({'组织' if c.is_organization else '角色'}, {c.role_type}): {c.personality[:100] if c.personality else ''}"
                    for c in characters
                ]) if characters else '暂无角色信息',
                'chapter_outline': outline.content if outline else chapter.summary or '暂无大纲',
                'previous_context': ''  # 可以后续扩展添加前置章节上下文
            }
        finally:
            await temp_db.close()
        break
    
    async def event_generator():
        """流式生成事件生成器"""
        db_session = None
        db_committed = False
        
        try:
            # 创建独立数据库会话
            async for db_session in get_db(request):
                # 发送开始事件
                yield f"data: {json.dumps({'type': 'start', 'message': '开始重新生成章节...'}, ensure_ascii=False)}\n\n"
                
                # 创建重新生成任务
                regen_task = RegenerationTask(
                    chapter_id=chapter_id,
                    analysis_id=analysis.id if analysis else None,
                    user_id=user_id,
                    project_id=chapter.project_id,
                    modification_instructions="",  # 稍后填充
                    original_suggestions=analysis.suggestions if analysis else None,
                    selected_suggestion_indices=regenerate_request.selected_suggestion_indices,
                    custom_instructions=regenerate_request.custom_instructions,
                    style_id=regenerate_request.style_id,
                    target_word_count=regenerate_request.target_word_count,
                    focus_areas=regenerate_request.focus_areas,
                    preserve_elements=regenerate_request.preserve_elements.model_dump() if regenerate_request.preserve_elements else None,
                    status='running',
                    original_content=chapter.content,
                    original_word_count=chapter.word_count or len(chapter.content),
                    version_note=regenerate_request.version_note,
                    started_at=datetime.now()
                )
                db_session.add(regen_task)
                await db_session.commit()
                await db_session.refresh(regen_task)
                
                task_id = regen_task.id
                logger.info(f"📝 创建重新生成任务: {task_id}")
                
                yield f"data: {json.dumps({'type': 'task_created', 'task_id': task_id}, ensure_ascii=False)}\n\n"
                
                # 初始化重新生成器
                regenerator = ChapterRegenerator(user_ai_service)
                
                # 流式生成新内容
                full_content = ""
                async for event in regenerator.regenerate_with_feedback(
                    chapter=chapter,
                    analysis=analysis,
                    regenerate_request=regenerate_request,
                    project_context=project_context
                ):
                    # 处理不同类型的事件
                    if event['type'] == 'chunk':
                        # 内容块
                        chunk = event['content']
                        full_content += chunk
                        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"
                    elif event['type'] == 'progress':
                        # 进度更新
                        progress_data = {
                            'type': 'progress',
                            'progress': event.get('progress', 0),
                            'message': event.get('message', ''),
                            'word_count': event.get('word_count', 0)
                        }
                        yield f"data: {json.dumps(progress_data, ensure_ascii=False)}\n\n"
                    
                    await asyncio.sleep(0)
                
                # 更新任务状态
                regen_task.status = 'completed'
                regen_task.regenerated_content = full_content
                regen_task.regenerated_word_count = len(full_content)
                regen_task.completed_at = datetime.now()
                
                # 计算差异统计
                diff_stats = regenerator.calculate_content_diff(chapter.content, full_content)
                
                await db_session.commit()
                db_committed = True
                
                # 先发送结果数据
                result_data = {
                    'type': 'result',
                    'data': {
                        'task_id': task_id,
                        'word_count': len(full_content),
                        'version_number': regen_task.version_number,
                        'auto_applied': regenerate_request.auto_apply,
                        'diff_stats': diff_stats
                    }
                }
                yield f"data: {json.dumps(result_data, ensure_ascii=False)}\n\n"
                
                # 再发送完成事件
                completion_data = {
                    'type': 'done',
                    'message': '重新生成完成'
                }
                yield f"data: {json.dumps(completion_data, ensure_ascii=False)}\n\n"
                
                logger.info(f"✅ 章节重新生成完成: {chapter_id}, 任务: {task_id}")
                
                break
        
        except Exception as e:
            logger.error(f"❌ 重新生成失败: {str(e)}", exc_info=True)
            
            # 更新任务状态为失败
            if db_session and not db_committed:
                try:
                    task_result = await db_session.execute(
                        select(RegenerationTask).where(RegenerationTask.chapter_id == chapter_id)
                        .order_by(RegenerationTask.created_at.desc()).limit(1)
                    )
                    task = task_result.scalar_one_or_none()
                    if task:
                        task.status = 'failed'
                        task.error_message = str(e)[:500]
                        task.completed_at = datetime.now()
                        await db_session.commit()
                except Exception as update_error:
                    logger.error(f"更新任务失败状态失败: {str(update_error)}")
            
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
        
        finally:
            if db_session:
                try:
                    if not db_committed and db_session.in_transaction():
                        await db_session.rollback()
                    await db_session.close()
                except Exception as close_error:
                    logger.error(f"关闭数据库会话失败: {str(close_error)}")
    
    return create_sse_response(event_generator())


@router.get("/{chapter_id}/regeneration/tasks", summary="获取章节的重新生成任务列表")
async def get_regeneration_tasks(
    chapter_id: str,
    request: Request,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db)
):
    """获取指定章节的重新生成任务历史"""
    user_id = getattr(request.state, 'user_id', None)
    
    # 验证章节存在和权限
    chapter_result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
    )
    chapter = chapter_result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    
    await verify_project_access(chapter.project_id, user_id, db)
    
    # 获取任务列表
    result = await db.execute(
        select(RegenerationTask)
        .where(RegenerationTask.chapter_id == chapter_id)
        .order_by(RegenerationTask.created_at.desc())
        .limit(limit)
    )
    tasks = result.scalars().all()
    
    return {
        "chapter_id": chapter_id,
        "total": len(tasks),
        "tasks": [
            {
                "task_id": task.id,
                "status": task.status,
                "version_number": task.version_number,
                "version_note": task.version_note,
                "original_word_count": task.original_word_count,
                "regenerated_word_count": task.regenerated_word_count,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None
            }
            for task in tasks
        ]
    }

