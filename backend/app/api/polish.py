"""AI去味API - 核心特色功能"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.generation_history import GenerationHistory
from app.schemas.polish import PolishRequest, PolishResponse
from app.services.ai_service import AIService
from app.services.corpus_bridge import CorpusBridge
from app.services.prompt_service import prompt_service
from app.logger import get_logger
from app.api.settings import get_user_ai_service, require_login
from app.user_manager import User

router = APIRouter(prefix="/polish", tags=["AI去味"])
logger = get_logger(__name__)


@router.post("", response_model=PolishResponse, summary="AI去味")
async def polish_text(
    request: PolishRequest,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
    user_ai_service: AIService = Depends(get_user_ai_service)
):
    """
    AI去味 - 将AI生成的文本改写得更像人类作家的手笔
    
    核心功能：
    - 去除AI痕迹（工整排比、重复修辞、机械总结）
    - 增加人性化（口语化、不完美细节、真实情感）
    - 优化叙事（自然节奏、简单词汇、松弛感）
    - 让对话更生活化
    
    这是本项目的核心特色功能！
    """
    try:
        corpus_highlights = await CorpusBridge(
            user.user_id,
            db,
        ).get_highlight_for_denoise(request.original_text, limit=5)

        # 构建AI去味提示词
        prompt = prompt_service.get_denoising_prompt(
            original_text=request.original_text,
            corpus_highlight_passages=corpus_highlights,
        )
        
        logger.info(f"开始AI去味处理，原文长度: {len(request.original_text)}")
        
        # 调用AI进行去味处理
        result = await user_ai_service.generate_text(
            prompt=prompt,
            provider=request.provider,
            model=request.model,
            temperature=request.temperature,
            max_tokens=len(request.original_text) * 2  # 预留足够token
        )
        polished_text = result.get("content", "") if isinstance(result, dict) else str(result)
        if not polished_text.strip():
            raise ValueError("AI服务返回空响应")
        
        # 计算字数
        word_count_before = len(request.original_text)
        word_count_after = len(polished_text)
        
        logger.info(f"AI去味完成，处理后长度: {word_count_after}")
        
        # 如果提供了项目ID，记录到历史
        if request.project_id:
            history = GenerationHistory(
                project_id=request.project_id,
                prompt=f"原文: {request.original_text[:100]}...",
                generated_content=polished_text,
                model=request.model or "default"
            )
            db.add(history)
            await db.commit()
        
        return PolishResponse(
            original_text=request.original_text,
            polished_text=polished_text,
            word_count_before=word_count_before,
            word_count_after=word_count_after
        )
        
    except Exception as e:
        logger.error(f"AI去味失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"AI去味失败: {str(e)}")


@router.post("/batch", summary="批量AI去味")
async def polish_batch(
    texts: list[str],
    project_id: int = None,
    provider: str = None,
    model: str = None,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
    user_ai_service: AIService = Depends(get_user_ai_service)
):
    """
    批量处理多个文本的AI去味
    
    适用于一次性处理多个章节或段落
    """
    try:
        results = []
        
        for idx, text in enumerate(texts):
            logger.info(f"处理第 {idx+1}/{len(texts)} 个文本")

            corpus_highlights = await CorpusBridge(
                user.user_id,
                db,
            ).get_highlight_for_denoise(text, limit=5)
            prompt = prompt_service.get_denoising_prompt(
                original_text=text,
                corpus_highlight_passages=corpus_highlights,
            )

            result = await user_ai_service.generate_text(
                prompt=prompt,
                provider=provider,
                model=model
            )
            polished_text = result.get("content", "") if isinstance(result, dict) else str(result)
            
            results.append({
                "index": idx,
                "original": text,
                "polished": polished_text,
                "word_count_before": len(text),
                "word_count_after": len(polished_text)
            })
        
        logger.info(f"批量AI去味完成，共处理 {len(results)} 个文本")
        
        return {
            "total": len(results),
            "results": results
        }
        
    except Exception as e:
        logger.error(f"批量AI去味失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"批量AI去味失败: {str(e)}")
