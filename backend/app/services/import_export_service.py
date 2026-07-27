"""导入导出服务"""
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.project import Project
from app.models.chapter import Chapter
from app.models.character import Character
from app.models.outline import Outline
from app.models.relationship import CharacterRelationship, Organization, OrganizationMember
from app.models.writing_style import WritingStyle
from app.models.generation_history import GenerationHistory
from app.schemas.import_export import (
    ProjectExportData,
    ChapterExportData,
    CharacterExportData,
    OutlineExportData,
    RelationshipExportData,
    OrganizationExportData,
    OrganizationMemberExportData,
    WritingStyleExportData,
    GenerationHistoryExportData,
    ImportValidationResult,
    ImportResult
)
from app.logger import get_logger

logger = get_logger(__name__)


class ImportExportService:
    """导入导出服务类"""
    
    SUPPORTED_VERSION = "1.0.0"
    
    @staticmethod
    async def export_project(
        project_id: str,
        db: AsyncSession,
        include_generation_history: bool = False,
        include_writing_styles: bool = True
    ) -> ProjectExportData:
        """
        导出项目完整数据
        
        参数：
            project_id: 项目ID
            db: 数据库会话
            include_generation_history: 是否包含生成历史
            include_writing_styles: 是否包含写作风格
            
        返回：
            ProjectExportData: 导出的项目数据
        """
        logger.info(f"开始导出项目: {project_id}")
        
        # 获取项目基本信息
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            raise ValueError(f"项目不存在: {project_id}")
        
        # 项目基本信息
        project_data = {
            "title": project.title,
            "description": project.description,
            "theme": project.theme,
            "genre": project.genre,
            "target_words": project.target_words,
            "current_words": project.current_words,
            "status": project.status,
            "world_time_period": project.world_time_period,
            "world_location": project.world_location,
            "world_atmosphere": project.world_atmosphere,
            "world_rules": project.world_rules,
            "chapter_count": project.chapter_count,
            "narrative_perspective": project.narrative_perspective,
            "character_count": project.character_count,
            "user_id": project.user_id,
            "created_at": project.created_at.isoformat() if project.created_at else None,
        }
        
        # 导出章节
        chapters = await ImportExportService._export_chapters(project_id, db)
        logger.info(f"导出章节数: {len(chapters)}")
        
        # 导出角色
        characters = await ImportExportService._export_characters(project_id, db)
        logger.info(f"导出角色数: {len(characters)}")
        
        # 导出大纲
        outlines = await ImportExportService._export_outlines(project_id, db)
        logger.info(f"导出大纲数: {len(outlines)}")
        
        # 导出关系
        relationships = await ImportExportService._export_relationships(project_id, db)
        logger.info(f"导出关系数: {len(relationships)}")
        
        # 导出组织详情
        organizations = await ImportExportService._export_organizations(project_id, db)
        logger.info(f"导出组织数: {len(organizations)}")
        
        # 导出组织成员
        org_members = await ImportExportService._export_organization_members(project_id, db)
        logger.info(f"导出组织成员数: {len(org_members)}")
        
        # 导出写作风格（可选）
        writing_styles = []
        if include_writing_styles:
            writing_styles = await ImportExportService._export_writing_styles(project_id, db)
            logger.info(f"导出写作风格数: {len(writing_styles)}")
        
        # 导出生成历史（可选）
        generation_history = []
        if include_generation_history:
            generation_history = await ImportExportService._export_generation_history(project_id, db)
            logger.info(f"导出生成历史数: {len(generation_history)}")
        
        export_data = ProjectExportData(
            version=ImportExportService.SUPPORTED_VERSION,
            export_time=datetime.utcnow().isoformat(),
            project=project_data,
            chapters=chapters,
            characters=characters,
            outlines=outlines,
            relationships=relationships,
            organizations=organizations,
            organization_members=org_members,
            writing_styles=writing_styles,
            generation_history=generation_history
        )
        
        logger.info(f"项目导出完成: {project_id}")
        return export_data
    
    @staticmethod
    async def _export_chapters(project_id: str, db: AsyncSession) -> List[ChapterExportData]:
        """导出章节"""
        result = await db.execute(
            select(Chapter)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.chapter_number)
        )
        chapters = result.scalars().all()
        
        return [
            ChapterExportData(
                title=ch.title,
                content=ch.content,
                summary=ch.summary,
                chapter_number=ch.chapter_number,
                word_count=ch.word_count or 0,
                status=ch.status,
                created_at=ch.created_at.isoformat() if ch.created_at else None
            )
            for ch in chapters
        ]
    
    @staticmethod
    async def _export_characters(project_id: str, db: AsyncSession) -> List[CharacterExportData]:
        """导出角色"""
        result = await db.execute(
            select(Character).where(Character.project_id == project_id)
        )
        characters = result.scalars().all()
        
        exported = []
        for char in characters:
            # 解析traits JSON
            traits = None
            if char.traits:
                try:
                    traits = json.loads(char.traits) if isinstance(char.traits, str) else char.traits
                except:
                    traits = None
            
            exported.append(CharacterExportData(
                name=char.name,
                age=char.age,
                gender=char.gender,
                is_organization=char.is_organization or False,
                role_type=char.role_type,
                personality=char.personality,
                background=char.background,
                appearance=char.appearance,
                traits=traits,
                organization_type=char.organization_type,
                organization_purpose=char.organization_purpose,
                created_at=char.created_at.isoformat() if char.created_at else None
            ))
        
        return exported
    
    @staticmethod
    async def _export_outlines(project_id: str, db: AsyncSession) -> List[OutlineExportData]:
        """导出大纲"""
        result = await db.execute(
            select(Outline)
            .where(Outline.project_id == project_id)
            .order_by(Outline.order_index)
        )
        outlines = result.scalars().all()
        
        return [
            OutlineExportData(
                title=ol.title,
                content=ol.content,
                structure=ol.structure,
                order_index=ol.order_index,
                created_at=ol.created_at.isoformat() if ol.created_at else None
            )
            for ol in outlines
        ]
    
    @staticmethod
    async def _export_relationships(project_id: str, db: AsyncSession) -> List[RelationshipExportData]:
        """导出关系"""
        result = await db.execute(
            select(CharacterRelationship, Character)
            .join(Character, CharacterRelationship.character_from_id == Character.id)
            .where(CharacterRelationship.project_id == project_id)
        )
        relationships = result.all()
        
        exported = []
        for rel, char_from in relationships:
            # 获取目标角色名称
            target_result = await db.execute(
                select(Character).where(Character.id == rel.character_to_id)
            )
            char_to = target_result.scalar_one_or_none()
            
            if char_to:
                exported.append(RelationshipExportData(
                    source_name=char_from.name,
                    target_name=char_to.name,
                    relationship_name=rel.relationship_name,
                    intimacy_level=rel.intimacy_level or 50,
                    status=rel.status or "active",
                    description=rel.description,
                    started_at=rel.started_at
                ))
        
        return exported
    
    @staticmethod
    async def _export_organizations(project_id: str, db: AsyncSession) -> List[OrganizationExportData]:
        """导出组织详情"""
        result = await db.execute(
            select(Organization, Character)
            .join(Character, Organization.character_id == Character.id)
            .where(Organization.project_id == project_id)
        )
        organizations = result.all()
        
        exported = []
        for org, char in organizations:
            # 获取父组织名称
            parent_name = None
            if org.parent_org_id:
                parent_result = await db.execute(
                    select(Organization, Character)
                    .join(Character, Organization.character_id == Character.id)
                    .where(Organization.id == org.parent_org_id)
                )
                parent_data = parent_result.first()
                if parent_data:
                    parent_name = parent_data[1].name
            
            exported.append(OrganizationExportData(
                character_name=char.name,
                parent_org_name=parent_name,
                power_level=org.power_level or 50,
                member_count=org.member_count or 0,
                location=org.location,
                motto=org.motto,
                color=org.color
            ))
        
        return exported
    
    @staticmethod
    async def _export_organization_members(project_id: str, db: AsyncSession) -> List[OrganizationMemberExportData]:
        """导出组织成员"""
        result = await db.execute(
            select(OrganizationMember, Organization, Character)
            .join(Organization, OrganizationMember.organization_id == Organization.id)
            .join(Character, Organization.character_id == Character.id)
            .where(Organization.project_id == project_id)
        )
        members = result.all()
        
        exported = []
        for member, org, org_char in members:
            # 获取成员角色名称
            char_result = await db.execute(
                select(Character).where(Character.id == member.character_id)
            )
            member_char = char_result.scalar_one_or_none()
            
            if member_char:
                exported.append(OrganizationMemberExportData(
                    organization_name=org_char.name,
                    character_name=member_char.name,
                    position=member.position,
                    rank=member.rank or 0,
                    status=member.status or "active",
                    joined_at=member.joined_at,
                    loyalty=member.loyalty or 50,
                    contribution=member.contribution or 0,
                    notes=member.notes
                ))
        
        return exported
    
    @staticmethod
    async def _export_writing_styles(project_id: str, db: AsyncSession) -> List[WritingStyleExportData]:
        """导出写作风格"""
        result = await db.execute(
            select(WritingStyle)
            .where(WritingStyle.project_id == project_id)
            .order_by(WritingStyle.order_index)
        )
        styles = result.scalars().all()
        
        return [
            WritingStyleExportData(
                name=style.name,
                style_type=style.style_type,
                preset_id=style.preset_id,
                description=style.description,
                prompt_content=style.prompt_content,
                order_index=style.order_index or 0
            )
            for style in styles
        ]
    
    @staticmethod
    async def _export_generation_history(project_id: str, db: AsyncSession) -> List[GenerationHistoryExportData]:
        """导出生成历史"""
        result = await db.execute(
            select(GenerationHistory, Chapter)
            .outerjoin(Chapter, GenerationHistory.chapter_id == Chapter.id)
            .where(GenerationHistory.project_id == project_id)
            .order_by(GenerationHistory.created_at.desc())
            .limit(100)  # 限制最多导出100条历史记录
        )
        histories = result.all()
        
        return [
            GenerationHistoryExportData(
                chapter_title=chapter.title if chapter else None,
                prompt=history.prompt,
                generated_content=history.generated_content,
                model=history.model,
                tokens_used=history.tokens_used,
                generation_time=history.generation_time,
                created_at=history.created_at.isoformat() if history.created_at else None
            )
            for history, chapter in histories
        ]
    
    @staticmethod
    def validate_import_data(data: Dict) -> ImportValidationResult:
        """
        验证导入数据
        
        参数：
            data: 导入的JSON数据
            
        返回：
            ImportValidationResult: 验证结果
        """
        errors = []
        warnings = []
        statistics = {}
        
        # 检查版本
        version = data.get("version", "")
        if not version:
            errors.append("缺少版本信息")
        elif version != ImportExportService.SUPPORTED_VERSION:
            warnings.append(f"版本不匹配: 导入文件版本为 {version}, 当前支持版本为 {ImportExportService.SUPPORTED_VERSION}")
        
        # 检查必需字段
        if "project" not in data:
            errors.append("缺少项目信息")
        else:
            project = data["project"]
            if not project.get("title"):
                errors.append("项目标题不能为空")
        
        # 统计数据
        statistics = {
            "chapters": len(data.get("chapters", [])),
            "characters": len(data.get("characters", [])),
            "outlines": len(data.get("outlines", [])),
            "relationships": len(data.get("relationships", [])),
            "organizations": len(data.get("organizations", [])),
            "organization_members": len(data.get("organization_members", [])),
            "writing_styles": len(data.get("writing_styles", [])),
            "generation_history": len(data.get("generation_history", []))
        }
        
        # 检查数据完整性
        if statistics["chapters"] == 0:
            warnings.append("项目没有章节数据")
        
        if statistics["characters"] == 0:
            warnings.append("项目没有角色数据")
        
        project_name = data.get("project", {}).get("title", "未知项目")
        
        return ImportValidationResult(
            valid=len(errors) == 0,
            version=version,
            project_name=project_name,
            statistics=statistics,
            errors=errors,
            warnings=warnings
        )
    
    @staticmethod
    async def import_project(
        data: Dict,
        db: AsyncSession,
        user_id: str
    ) -> ImportResult:
        """
        导入项目数据（创建新项目）
        
        参数：
            data: 导入的JSON数据
            db: 数据库会话
            user_id: 目标用户ID（导入后的项目归属）
            
        返回：
            ImportResult: 导入结果
        """
        warnings = []
        statistics = {}
        
        try:
            # 验证数据
            validation = ImportExportService.validate_import_data(data)
            if not validation.valid:
                return ImportResult(
                    success=False,
                    message=f"数据验证失败: {', '.join(validation.errors)}",
                    statistics={},
                    warnings=validation.warnings
                )
            
            warnings.extend(validation.warnings)
            
            logger.info(f"开始导入项目: {validation.project_name}")
            
            # 创建项目
            project_data = data["project"]
            new_project = Project(
                user_id=user_id,  # 设置为当前用户ID
                title=project_data.get("title"),
                description=project_data.get("description"),
                theme=project_data.get("theme"),
                genre=project_data.get("genre"),
                target_words=project_data.get("target_words"),
                status=project_data.get("status", "planning"),
                world_time_period=project_data.get("world_time_period"),
                world_location=project_data.get("world_location"),
                world_atmosphere=project_data.get("world_atmosphere"),
                world_rules=project_data.get("world_rules"),
                chapter_count=project_data.get("chapter_count"),
                narrative_perspective=project_data.get("narrative_perspective"),
                character_count=project_data.get("character_count"),
                current_words=project_data.get("current_words", 0),  # 保留原项目的字数
                wizard_step=4,  # 导入的项目设置为向导完成状态
                wizard_status="completed"  # 标记向导已完成
            )
            db.add(new_project)
            await db.flush()  # 获取project_id
            
            logger.info(f"创建项目成功: {new_project.id}")
            
            # 导入章节
            chapters_count = await ImportExportService._import_chapters(
                new_project.id, data.get("chapters", []), db
            )
            statistics["chapters"] = chapters_count
            logger.info(f"导入章节数: {chapters_count}")
            
            # 导入角色（包括组织）
            char_mapping = await ImportExportService._import_characters(
                new_project.id, data.get("characters", []), db
            )
            statistics["characters"] = len(char_mapping)
            logger.info(f"导入角色数: {len(char_mapping)}")
            
            # 导入大纲
            outlines_count = await ImportExportService._import_outlines(
                new_project.id, data.get("outlines", []), db
            )
            statistics["outlines"] = outlines_count
            logger.info(f"导入大纲数: {outlines_count}")
            
            # 导入关系
            relationships_count = await ImportExportService._import_relationships(
                new_project.id, data.get("relationships", []), char_mapping, db
            )
            statistics["relationships"] = relationships_count
            logger.info(f"导入关系数: {relationships_count}")
            
            # 导入组织详情
            org_mapping = await ImportExportService._import_organizations(
                new_project.id, data.get("organizations", []), char_mapping, db
            )
            statistics["organizations"] = len(org_mapping)
            logger.info(f"导入组织数: {len(org_mapping)}")
            
            # 导入组织成员
            org_members_count = await ImportExportService._import_organization_members(
                data.get("organization_members", []), char_mapping, org_mapping, db
            )
            statistics["organization_members"] = org_members_count
            logger.info(f"导入组织成员数: {org_members_count}")
            
            # 导入写作风格
            styles_count = await ImportExportService._import_writing_styles(
                new_project.id, data.get("writing_styles", []), db
            )
            statistics["writing_styles"] = styles_count
            logger.info(f"导入写作风格数: {styles_count}")
            
            # 提交事务
            await db.commit()
            
            logger.info(f"项目导入完成: {new_project.id}")
            
            return ImportResult(
                success=True,
                project_id=new_project.id,
                message="项目导入成功",
                statistics=statistics,
                warnings=warnings
            )
            
        except Exception as e:
            await db.rollback()
            logger.error(f"导入项目失败: {str(e)}", exc_info=True)
            return ImportResult(
                success=False,
                message=f"导入失败: {str(e)}",
                statistics=statistics,
                warnings=warnings
            )
    
    @staticmethod
    async def _import_chapters(
        project_id: str,
        chapters_data: List[Dict],
        db: AsyncSession
    ) -> int:
        """导入章节"""
        count = 0
        for ch_data in chapters_data:
            chapter = Chapter(
                project_id=project_id,
                title=ch_data.get("title"),
                content=ch_data.get("content"),
                summary=ch_data.get("summary"),
                chapter_number=ch_data.get("chapter_number"),
                word_count=ch_data.get("word_count", 0),
                status=ch_data.get("status", "draft")
            )
            db.add(chapter)
            count += 1
        
        return count
    
    @staticmethod
    async def _import_characters(
        project_id: str,
        characters_data: List[Dict],
        db: AsyncSession
    ) -> Dict[str, str]:
        """导入角色，返回名称到ID的映射"""
        char_mapping = {}
        
        for char_data in characters_data:
            # 处理traits
            traits = char_data.get("traits")
            if isinstance(traits, list):
                traits = json.dumps(traits, ensure_ascii=False)
            
            character = Character(
                project_id=project_id,
                name=char_data.get("name"),
                age=char_data.get("age"),
                gender=char_data.get("gender"),
                is_organization=char_data.get("is_organization", False),
                role_type=char_data.get("role_type"),
                personality=char_data.get("personality"),
                background=char_data.get("background"),
                appearance=char_data.get("appearance"),
                traits=traits,
                organization_type=char_data.get("organization_type"),
                organization_purpose=char_data.get("organization_purpose")
            )
            db.add(character)
            await db.flush()  # 获取ID
            char_mapping[char_data.get("name")] = character.id
        
        return char_mapping
    
    @staticmethod
    async def _import_outlines(
        project_id: str,
        outlines_data: List[Dict],
        db: AsyncSession
    ) -> int:
        """导入大纲"""
        count = 0
        for ol_data in outlines_data:
            outline = Outline(
                project_id=project_id,
                title=ol_data.get("title"),
                content=ol_data.get("content"),
                structure=ol_data.get("structure"),
                order_index=ol_data.get("order_index")
            )
            db.add(outline)
            count += 1
        
        return count
    
    @staticmethod
    async def _import_relationships(
        project_id: str,
        relationships_data: List[Dict],
        char_mapping: Dict[str, str],
        db: AsyncSession
    ) -> int:
        """导入关系"""
        count = 0
        for rel_data in relationships_data:
            source_name = rel_data.get("source_name")
            target_name = rel_data.get("target_name")
            
            # 查找角色ID
            source_id = char_mapping.get(source_name)
            target_id = char_mapping.get(target_name)
            
            if source_id and target_id:
                relationship = CharacterRelationship(
                    project_id=project_id,
                    character_from_id=source_id,
                    character_to_id=target_id,
                    relationship_name=rel_data.get("relationship_name"),
                    intimacy_level=rel_data.get("intimacy_level", 50),
                    status=rel_data.get("status", "active"),
                    description=rel_data.get("description"),
                    started_at=rel_data.get("started_at")
                )
                db.add(relationship)
                count += 1
        
        return count
    
    @staticmethod
    async def _import_organizations(
        project_id: str,
        organizations_data: List[Dict],
        char_mapping: Dict[str, str],
        db: AsyncSession
    ) -> Dict[str, str]:
        """导入组织详情，返回名称到ID的映射"""
        org_mapping = {}
        
        # 第一遍：创建所有组织（不设置父组织）
        temp_orgs = []
        for org_data in organizations_data:
            char_name = org_data.get("character_name")
            char_id = char_mapping.get(char_name)
            
            if char_id:
                organization = Organization(
                    project_id=project_id,
                    character_id=char_id,
                    power_level=org_data.get("power_level", 50),
                    member_count=org_data.get("member_count", 0),
                    location=org_data.get("location"),
                    motto=org_data.get("motto"),
                    color=org_data.get("color")
                )
                db.add(organization)
                temp_orgs.append((organization, org_data.get("parent_org_name")))
        
        await db.flush()  # 获取所有组织的ID
        
        # 建立名称到ID的映射
        for org, _ in temp_orgs:
            # 通过character_id查找角色名
            result = await db.execute(
                select(Character).where(Character.id == org.character_id)
            )
            char = result.scalar_one_or_none()
            if char:
                org_mapping[char.name] = org.id
        
        # 第二遍：设置父组织关系
        for org, parent_name in temp_orgs:
            if parent_name:
                parent_id = org_mapping.get(parent_name)
                if parent_id:
                    org.parent_org_id = parent_id
        
        return org_mapping
    
    @staticmethod
    async def _import_organization_members(
        org_members_data: List[Dict],
        char_mapping: Dict[str, str],
        org_mapping: Dict[str, str],
        db: AsyncSession
    ) -> int:
        """导入组织成员"""
        count = 0
        for member_data in org_members_data:
            org_name = member_data.get("organization_name")
            char_name = member_data.get("character_name")
            
            org_id = org_mapping.get(org_name)
            char_id = char_mapping.get(char_name)
            
            if org_id and char_id:
                member = OrganizationMember(
                    organization_id=org_id,
                    character_id=char_id,
                    position=member_data.get("position"),
                    rank=member_data.get("rank", 0),
                    status=member_data.get("status", "active"),
                    joined_at=member_data.get("joined_at"),
                    loyalty=member_data.get("loyalty", 50),
                    contribution=member_data.get("contribution", 0),
                    notes=member_data.get("notes")
                )
                db.add(member)
                count += 1
        
        return count
    
    @staticmethod
    async def _import_writing_styles(
        project_id: str,
        styles_data: List[Dict],
        db: AsyncSession
    ) -> int:
        """导入写作风格"""
        count = 0
        for style_data in styles_data:
            style = WritingStyle(
                project_id=project_id,
                name=style_data.get("name"),
                style_type=style_data.get("style_type"),
                preset_id=style_data.get("preset_id"),
                description=style_data.get("description"),
                prompt_content=style_data.get("prompt_content"),
                order_index=style_data.get("order_index", 0)
            )
            db.add(style)
            count += 1
        
        return count