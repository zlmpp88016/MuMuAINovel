"""提示词管理服务"""
from typing import Dict, Any, Optional
import json


class WritingStyleManager:
    """写作风格管理器"""
    
    # 预设风格配置
    PRESET_STYLES = {
        "natural": {
            "name": "自然流畅",
            "description": "像普通人讲故事一样自然，不刻意修饰，有生活气息",
            "prompt_content": """
**自然流畅风格要求：**
- 用简单朴实的语言叙述，避免华丽辞藻
- 像在和朋友聊天一样讲故事
- 保持轻松自然的节奏，不要刻意营造氛围
- 多用短句，少用长句和排比
- 让读者感觉舒服，不要让人觉得在"看文学作品"
"""
        },
        "classical": {
            "name": "古典优雅",
            "description": "典雅精致的文学风格，注重意境和韵味",
            "prompt_content": """
**古典优雅风格要求：**
- 使用优美典雅的语言，注重文字的韵律感
- 善用比喻、拟人等修辞手法
- 注重意境营造，追求诗意美感
- 可适当引用古诗词或典故（需符合世界观）
- 保持端庄雅致的叙述节奏
"""
        },
        "modern": {
            "name": "现代简约",
            "description": "简洁明快的现代风格，注重效率和直接表达",
            "prompt_content": """
**现代简约风格要求：**
- 语言简洁有力，直达重点
- 多用短句和短段落，节奏明快
- 避免冗长描写，注重信息密度
- 使用现代口语化表达
- 情节推进快速，少做环境渲染
"""
        },
        "poetic": {
            "name": "诗意抒情",
            "description": "富有诗意和情感张力的抒情风格",
            "prompt_content": """
**诗意抒情风格要求：**
- 注重情感表达和内心描写
- 善用景物描写烘托情绪
- 语言富有韵律和美感
- 细腻刻画人物心理活动
- 营造情感氛围，引发共鸣
"""
        },
        "concise": {
            "name": "精炼利落",
            "description": "惜字如金的简练风格，每个字都有意义",
            "prompt_content": """
**精炼利落风格要求：**
- 删除所有冗余描写，每句话都要有作用
- 多用动词，少用形容词和副词
- 对话干脆利落，不拖泥带水
- 环境描写点到为止
- 用最少的字数传达最多的信息
"""
        },
        "vivid": {
            "name": "生动形象",
            "description": "画面感强烈，让读者如临其境",
            "prompt_content": """
**生动形象风格要求：**
- 注重细节描写，让场景具体可感
- 调动五感（视觉、听觉、触觉、嗅觉、味觉）
- 使用鲜明的比喻和形象化语言
- 让读者能"看到"场景和动作
- 人物表情、动作要具体生动
"""
        }
    }
    
    @classmethod
    def get_preset_style(cls, preset_id: str) -> Optional[Dict[str, str]]:
        """获取预设风格配置"""
        return cls.PRESET_STYLES.get(preset_id)
    
    @classmethod
    def get_all_presets(cls) -> Dict[str, Dict[str, str]]:
        """获取所有预设风格"""
        return cls.PRESET_STYLES
    
    @staticmethod
    def apply_style_to_prompt(base_prompt: str, style_content: str) -> str:
        """
        将写作风格应用到基础提示词中
        
        Args:
            base_prompt: 基础提示词
            style_content: 风格要求内容
            
        Returns:
            组合后的提示词
        """
        # 在基础提示词末尾添加风格要求
        return f"{base_prompt}\n\n{style_content}\n\n请直接输出章节正文内容，不要包含章节标题和其他说明文字。"


class PromptService:
    """提示词模板管理"""
    
    # 世界构建提示词
    WORLD_BUILDING = """你是一位资深的世界观设计师。请根据以下信息构建一个完整的小说世界观：

书名：{title}
主题：{theme}
类型：{genre}

请生成包含以下内容的世界构建框架：

1. **时间背景**：具体的时代设定、时间流逝特点、重要历史事件
2. **地理位置**：主要地点描述、地理环境特征、空间布局
3. **氛围基调**：整体氛围感觉、情感色彩、视觉风格
4. **世界规则**：基本运行法则、特殊设定、社会规则和禁忌、权力结构

要求：
- 与主题高度契合
- 设定要合理自洽
- 为故事发展提供支撑
- 具有独特性和吸引力

**重要格式要求：**
1. 只返回纯JSON格式，不要包含任何markdown标记、代码块标记或其他说明文字
2. 不要在JSON字符串值中使用中文引号（""''），请使用英文引号或直接省略引号
3. 专有名词和强调内容可以使用【】或《》标记，不要用引号

请严格按照以下JSON格式返回（每个字段为200-300字的文本描述）：
{{
  "time_period": "时间背景的详细描述，包括时代设定、时间特点、历史事件",
  "location": "地理位置的详细描述，包括主要地点、环境特征、空间布局",
  "atmosphere": "氛围基调的详细描述，包括整体氛围、情感色彩、视觉风格",
  "rules": "世界规则的详细描述，包括运行法则、特殊设定、社会规则、权力结构"
}}

再次强调：
1. 只返回纯JSON对象，不要有```json```这样的标记
2. 文本中不要使用中文引号（""），使用【】或《》代替
3. 不要有任何额外的文字说明"""

    # 批量角色生成提示词
    CHARACTERS_BATCH_GENERATION = """你是一位专业的角色设定师。请根据以下世界观和要求，生成{count}个立体丰满的角色和组织：

世界观信息：
- 时间背景：{time_period}
- 地理位置：{location}
- 氛围基调：{atmosphere}
- 世界规则：{rules}

主题：{theme}
类型：{genre}
特殊要求：{requirements}

【数量要求 - 必须严格遵守】
请精确生成{count}个实体，不多不少。数组中必须包含且仅包含{count}个对象。

实体类型分配：
- 至少1个主角（protagonist）
- 多个配角（supporting）
- 可以包含反派（antagonist）
- 可以包含1-2个**高影响力的重要组织**（势力等级应在70-95之间）

要求：
- 角色要符合世界观设定
- 性格和背景要有深度
- 角色之间要有关系网络
- 组织要有存在的合理性
- 所有实体要为故事服务

**重要格式要求：**
1. 只返回纯JSON数组格式，不要包含任何markdown标记、代码块标记或其他说明文字
2. 不要在JSON字符串值中使用中文引号（""''），请使用英文引号或【】《》标记
3. 专有名词和强调内容使用【】或《》，不要用引号

请严格按照以下JSON数组格式返回（每个角色为数组中的一个对象）：
[
  {{
    "name": "角色姓名",
    "age": 25,
    "gender": "男/女/其他",
    "is_organization": false,
    "role_type": "protagonist/supporting/antagonist",
    "personality": "性格特点的详细描述（100-200字），包括核心性格、优缺点、特殊习惯",
    "background": "背景故事的详细描述（100-200字），包括家庭背景、成长经历、重要转折",
    "appearance": "外貌描述（50-100字），包括身高、体型、面容、着装风格",
    "traits": ["特长1", "特长2", "特长3"],
    "relationships_array": [
      {{
        "target_character_name": "已生成的角色名称",
        "relationship_type": "关系类型（师父/朋友/敌人/父亲/母亲等）",
        "intimacy_level": 75,
        "description": "关系描述"
      }}
    ],
    "organization_memberships": [
      {{
        "organization_name": "已生成的组织名称",
        "position": "职位",
        "rank": 5,
        "loyalty": 80
      }}
    ]
  }},
  {{
    "name": "组织名称",
    "is_organization": true,
    "role_type": "supporting",
    "personality": "组织特性描述（100-200字），包括运作方式、核心理念、行事风格",
    "background": "组织背景（100-200字），包括建立历史、发展历程、重要事件",
    "appearance": "组织外在表现（50-100字），如总部位置、标志性建筑等",
    "organization_type": "组织类型",
    "organization_purpose": "组织目的",
    "organization_members": ["成员1", "成员2"],
    "power_level": 85,
    "location": "组织所在地或主要活动区域",
    "motto": "组织格言、口号或宗旨",
    "color": "组织代表颜色（如：深红色、金色、黑色等）",
    "traits": []
  }}
]

**组织生成要求（重要）：**
- 组织必须是对故事有重大影响的势力
- power_level应在70-95之间（高影响力组织）
- 不要生成无关紧要的小组织或普通社团
- 组织应该是推动剧情发展的关键力量
- 可以是正派势力、中立势力或反派势力，但一定要有存在感

**关系类型参考（从中选择或自定义）：**
- 家族：父亲、母亲、兄弟、姐妹、子女、配偶、恋人
- 社交：师父、徒弟、朋友、同学、同事、邻居、知己
- 职业：上司、下属、合作伙伴
- 敌对：敌人、仇人、竞争对手、宿敌

**重要说明：**
1. **数量控制**：数组中必须精确包含{count}个对象，不能多也不能少
2. **关系约束**：relationships_array只能引用本批次中已经出现的角色名称
3. **组织约束**：organization_memberships只能引用本批次中is_organization=true的实体名称
4. **禁止幻觉**：不要引用任何不存在的角色或组织，如果没有可引用的就留空数组[]
5. intimacy_level是-100到100的整数（负值表示敌对仇恨关系），loyalty是0-100的整数
6. 角色之间要形成合理的关系网络

**示例说明**：
- 如果生成了角色A、组织B、角色C，则角色A的organization_memberships只能是[组织B]，不能是其他组织
- 如果角色A在数组第一位，它的relationships_array必须为空[]，因为还没有其他角色
- 如果角色C在数组第三位，它的relationships_array可以引用角色A，但不能引用不存在的角色D

再次强调：
1. 只返回纯JSON数组，不要有```json```这样的标记
2. 数组中必须精确包含{count}个对象
3. 不要引用任何本批次中不存在的角色或组织名称
4. 文本描述中不要使用中文引号（""），改用【】或《》"""

    # 向导大纲生成提示词
    COMPLETE_OUTLINE_GENERATION = """你是一位经验丰富的小说作家和编剧。请根据以下信息生成完整的{chapter_count}章小说大纲：

基本信息：
- 书名：{title}
- 主题：{theme}
- 类型：{genre}
- 章节数：{chapter_count}
- 叙事视角：{narrative_perspective}
- 目标字数：{target_words}

世界观：
- 时间背景：{time_period}
- 地理位置：{location}
- 氛围基调：{atmosphere}
- 世界规则：{rules}

角色信息：
{characters_info}

{mcp_references}

其他要求：{requirements}

整体要求：
- 结构完整：起承转合清晰
- 情节连贯：章节之间紧密衔接
- 冲突递进：矛盾逐步升级
- 人物成长：角色有明确的变化弧线
- 节奏把控：有张有弛
- 视角统一：采用{narrative_perspective}视角叙事

**重要格式要求：**
1. 只返回纯JSON数组格式，不要包含任何markdown标记、代码块标记或其他说明文字
2. 不要在JSON字符串值中使用中文引号（""''），请使用【】或《》标记
3. 专有名词、书名、事件名使用【】或《》

请严格按照以下JSON数组格式返回（共{chapter_count}个章节对象）：
[
  {{
    "chapter_number": 1,
    "title": "第一章标题",
    "summary": "章节概要的详细描述（100-200字），包含主要情节、冲突、转折等",
    "scenes": ["场景1描述", "场景2描述", "场景3描述"],
    "characters": ["角色1", "角色2"],
    "key_points": ["情节要点1", "情节要点2"],
    "emotion": "本章情感基调",
    "goal": "本章叙事目标"
  }},
  {{
    "chapter_number": 2,
    "title": "第二章标题",
    "summary": "章节概要...",
    "scenes": ["场景1", "场景2"],
    "characters": ["角色1", "角色2"],
    "key_points": ["要点1", "要点2"],
    "emotion": "情感基调",
    "goal": "叙事目标"
  }}
]

再次强调：
1. 只返回纯JSON数组，不要有```json```这样的标记
2. 数组中要包含{chapter_count}个章节对象
3. 文本中不要使用中文引号（""），改用【】或《》"""
    
    # 大纲续写提示词（记忆增强版）
    OUTLINE_CONTINUE_GENERATION = """你是一位经验丰富的小说作家和编剧。请基于以下信息续写小说大纲：

【项目信息】
- 书名：{title}
- 主题：{theme}
- 类型：{genre}
- 叙事视角：{narrative_perspective}
- 续写章节数：{chapter_count}章

【世界观】
- 时间背景：{time_period}
- 地理位置：{location}
- 氛围基调：{atmosphere}
- 世界规则：{rules}

【角色信息】
{characters_info}

【已有章节概览】（共{current_chapter_count}章）
{all_chapters_brief}

【最近剧情】
{recent_plot}

【🧠 智能记忆系统 - 续写参考】
以下是从故事记忆库中检索到的相关信息，请在续写大纲时参考：

{memory_context}

{mcp_references}

【续写指导】
- 当前情节阶段：{plot_stage_instruction}
- 起始章节编号：第{start_chapter}章
- 故事发展方向：{story_direction}
- 其他要求：{requirements}

请生成第{start_chapter}章到第{end_chapter}章的大纲。
要求：
- **剧情连贯性**：与前文自然衔接，保持故事连贯性
- **记忆参考**：适当参考记忆系统中的伏笔、钩子和情节点
- **伏笔回收**：可以考虑回收未完结的伏笔，制造呼应
- **角色发展**：遵循角色在前文中的成长轨迹
- **情节阶段**：遵循情节阶段的发展要求
- **风格一致**：保持与已有章节相同的风格和详细程度

**重要格式要求：**
1. 只返回纯JSON数组格式，不要包含任何markdown标记、代码块标记或其他说明文字
2. 不要在JSON字符串值中使用中文引号（""''），请使用【】或《》
3. 文本描述中的专有名词使用【】标记

请严格按照以下JSON数组格式返回（共{chapter_count}个章节对象）：
[
  {{
    "chapter_number": {start_chapter},
    "title": "章节标题",
    "summary": "章节概要的详细描述（100-200字），包含主要情节、角色互动、关键事件、冲突与转折",
    "scenes": ["场景1描述", "场景2描述", "场景3描述"],
    "characters": ["涉及角色1", "涉及角色2"],
    "key_points": ["情节要点1", "情节要点2"],
    "emotion": "本章情感基调",
    "goal": "本章叙事目标"
  }},
  {{
    "chapter_number": {start_chapter} + 1,
    "title": "章节标题",
    "summary": "章节概要...",
    "scenes": ["场景1", "场景2"],
    "characters": ["角色1", "角色2"],
    "key_points": ["要点1", "要点2"],
    "emotion": "情感基调",
    "goal": "叙事目标"
  }}
]

再次强调：
1. 只返回纯JSON数组，不要有```json```这样的标记
2. 数组中要包含{chapter_count}个章节对象
3. 每个summary必须是100-200字的详细描述
4. 确保字段结构与已有章节完全一致
5. 文本中不要使用中文引号（""），改用【】或《》"""
    
    # AI去味提示词（核心特色功能）
    AI_DENOISING = """你是一位追求自然写作风格的编辑。你的任务是将AI生成的文本改写得更像人类作家的手笔。

原文：
{original_text}

修改要求：
1. 去除AI痕迹：
   - 删除过于工整的排比句
   - 减少重复的修辞手法
   - 去掉刻意的对称结构
   - 避免机械式的总结陈词

2. 增加人性化：
   - 使用更口语化的表达
   - 添加不完美的细节
   - 保留适度的随意性
   - 增加真实的情感波动

3. 优化叙事：
   - 让节奏更自然不做作
   - 用简单词汇替换华丽辞藻
   - 保持叙述的松弛感
   - 让对话更生活化

4. 保持原意：
   - 不改变核心情节
   - 保留关键信息点
   - 维持角色性格
   - 确保逻辑连贯

修改风格：
- 像是一个喜欢讲故事的普通人写的
- 有点粗糙但很真诚
- 自然流畅不刻意
- 让人读起来很舒服

请直接输出修改后的文本，无需解释。"""

    # 章节完整创作提示词
    CHAPTER_GENERATION = """你是一位专业的小说作家。请根据以下信息创作本章内容：

项目信息：
- 书名：{title}
- 主题：{theme}
- 类型：{genre}
- 叙事视角：{narrative_perspective}

世界观：
- 时间背景：{time_period}
- 地理位置：{location}
- 氛围基调：{atmosphere}
- 世界规则：{rules}

角色信息：
{characters_info}

全书大纲：
{outlines_context}

本章信息：
- 章节序号：第{chapter_number}章
- 章节标题：{chapter_title}
- 章节大纲：{chapter_outline}

创作要求：
1. 严格按照大纲内容展开情节
2. 保持与前后章节的连贯性
3. 符合角色性格设定
4. 体现世界观特色
5. 使用{narrative_perspective}视角
6. **字数要求：目标{target_word_count}字，不得低于{target_word_count}字，建议控制在{target_word_count}至{max_word_count}字之间**
7. 语言自然流畅，避免AI痕迹

请直接输出章节正文内容，不要包含章节标题和其他说明文字。"""

    # 章节完整创作提示词（带前置章节上下文和记忆增强）
    CHAPTER_GENERATION_WITH_CONTEXT = """你是一位专业的小说作家。请根据以下信息创作本章内容：

项目信息：
- 书名：{title}
- 主题：{theme}
- 类型：{genre}
- 叙事视角：{narrative_perspective}

世界观：
- 时间背景：{time_period}
- 地理位置：{location}
- 氛围基调：{atmosphere}
- 世界规则：{rules}

角色信息：
{characters_info}

全书大纲：
{outlines_context}

【已完成的前置章节内容】
{previous_content}

【🧠 智能记忆系统 - 重要参考】
以下是从故事记忆库中检索到的相关信息，请在创作时适当参考和呼应：

{memory_context}

本章信息：
- 章节序号：第{chapter_number}章
- 章节标题：{chapter_title}
- 章节大纲：{chapter_outline}

创作要求：
1. **剧情连贯性（最重要）**：
- 必须承接前面章节的剧情发展
- 注意角色状态、情节进展、时间线的连续性
- 不能出现与前文矛盾的内容
- 自然过渡，避免突兀的跳跃

2. **情节推进**：
- 严格按照本章大纲展开情节
- 推动故事向前发展
- 保持与全书大纲的一致性

3. **角色一致性**：
- 符合角色性格设定
- 延续角色在前文中的成长和变化
- 保持角色关系的连贯性

4. **写作风格**：
- 使用{narrative_perspective}视角
- **字数要求：目标{target_word_count}字，不得低于{target_word_count}字，建议控制在{target_word_count}至{max_word_count}字之间**
- 语言自然流畅，避免AI痕迹
- 体现世界观特色

5. **承上启下**：
   - 开头自然衔接上一章结尾
   - 结尾为下一章做好铺垫

6. **记忆系统使用指南**：
   - **最近章节记忆**：保持情节连贯，注意角色状态和剧情发展
   - **语义相关记忆**：参考相似情节的处理方式
   - **未完结伏笔**：适当时机可以回收伏笔，制造呼应效果
   - **角色状态记忆**：确保角色行为符合其发展轨迹
   - **重要情节点**：与关键剧情保持一致

请直接输出章节正文内容，不要包含章节标题和其他说明文字。"""

    # 大纲生成提示词
    OUTLINE_GENERATION = """你是一位经验丰富的小说作家和编剧。请根据以下信息生成小说大纲：

类型：{genre}
主题：{theme}
目标字数：{target_words}
其他要求：{requirements}

请生成一个完整的章节大纲框架，包含：
1. 合理的章节数量（根据字数）
2. 每章的标题和内容概要
3. 清晰的故事结构（起承转合）
4. 情节的递进和冲突升级
5. 角色的成长弧线

**重要格式要求：**
1. 只返回纯JSON格式，不要包含任何markdown标记、代码块标记或其他说明文字
2. 不要在JSON字符串值中使用中文引号（""''），改用【】或《》
3. 专有名词和强调内容使用【】标记

请严格按照以下JSON格式返回：
{{
  "chapters": [
    {{
      "order": 1,
      "title": "章节标题",
      "content": "章节内容概要（150-200字）"
    }}
  ]
}}

再次强调：
1. 只返回纯JSON对象，不要有```json```这样的标记
2. 文本中不要使用中文引号（""），改用【】或《》
3. 不要有任何额外的文字说明"""

    # 单个角色生成提示词
    SINGLE_CHARACTER_GENERATION = """你是一位专业的角色设定师。请根据以下信息创建一个立体饱满的小说角色。

{project_context}

{user_input}

请生成一个完整的角色卡片，包含以下所有信息：

1. **基本信息**：
   - 姓名：如果用户未提供，请生成一个符合世界观的名字
   - 年龄：具体数字或年龄段
   - 性别：男/女/其他

2. **外貌特征**（100-150字）：
   - 身高体型、面容特征、着装风格
   - 要符合角色定位和世界观设定

3. **性格特点**（150-200字）：
   - 核心性格特质（至少3个）
   - 优点和缺点
   - 特殊习惯或癖好
   - 性格要有复杂性和矛盾性

4. **背景故事**（200-300字）：
   - 家庭背景
   - 成长经历
   - 重要转折事件
   - 如何与项目主题关联
   - 融入用户提供的背景设定

5. **人际关系**：
   - 与现有角色的关系（如果有）
   - 重要的人际纽带
   - 社会地位和人脉

6. **特殊能力/特长**：
   - 擅长的领域
   - 特殊技能或知识
   - 符合世界观设定

**重要格式要求：**
1. 只返回纯JSON格式，不要包含任何markdown标记、代码块标记或其他说明文字
2. 不要在JSON字符串值中使用中文引号（""''），改用【】或《》
3. 文本描述中的专有名词使用【】标记

请严格按照以下JSON格式返回：
{{
  "name": "角色姓名",
  "age": "年龄",
  "gender": "性别",
  "appearance": "外貌描述（100-150字）",
  "personality": "性格特点（150-200字）",
  "background": "背景故事（200-300字）",
  "traits": ["特长1", "特长2", "特长3"],
  
  "relationships_text": "人际关系的文字描述（用于显示）",
  
  "relationships": [
    {{
      "target_character_name": "已存在的角色名称",
      "relationship_type": "关系类型（如：师父、朋友、敌人、父亲、母亲等）",
      "intimacy_level": 75,
      "description": "这段关系的详细描述",
      "started_at": "关系开始的故事时间点（可选）"
    }}
  ],
  
  "organization_memberships": [
    {{
      "organization_name": "已存在的组织名称",
      "position": "职位名称",
      "rank": 8,
      "loyalty": 80,
      "joined_at": "加入时间（可选）",
      "status": "active"
    }}
  ]
}}

**关系类型参考（请从中选择或自定义）：**
- 家族关系：父亲、母亲、兄弟、姐妹、子女、配偶、恋人
- 社交关系：师父、徒弟、朋友、同学、同事、邻居、知己
- 职业关系：上司、下属、合作伙伴
- 敌对关系：敌人、仇人、竞争对手、宿敌

**重要说明：**
1. relationships数组：只包含与上面列出的已存在角色的关系，通过target_character_name匹配
2. organization_memberships数组：只包含与上面列出的已存在组织的关系
3. intimacy_level是-100到100的整数（负值表示敌对、仇恨等关系），loyalty是0-100的整数
4. 如果没有关系或组织，对应数组为空[]
5. relationships_text是自然语言描述，用于展示给用户看

**角色设定要求：**
- 角色要符合项目的世界观和主题
- 如果是主角，要有明确的成长空间和目标动机
- 如果是反派，要有合理的动机，不能脸谱化
- 配角要有独特性，不能是工具人
- 所有设定要为故事服务

再次强调：
1. 只返回纯JSON对象，不要有```json```这样的标记
2. 文本中不要使用中文引号（""），改用【】或《》
3. 不要有任何额外的文字说明"""

    # 单个组织生成提示词
    SINGLE_ORGANIZATION_GENERATION = """你是一位专业的组织设定师。请根据以下信息创建一个完整的组织/势力设定。

{project_context}

{user_input}

请生成一个完整的组织设定，包含以下所有信息：

1. **基本信息**：
   - 组织名称：如果用户未提供，请生成一个符合世界观的名称
   - 组织类型：如帮派、公司、门派、学院、政府机构、宗教组织等
   - 成立时间：具体时间或时间段

2. **组织特性**（150-200字）：
   - 组织的核心理念和行事风格
   - 组织文化和价值观
   - 运作方式和管理模式
   - 特殊传统或规矩

3. **组织背景**（200-300字）：
   - 建立历史和起源
   - 发展历程和重要事件
   - 目前的地位和影响力
   - 如何与项目主题关联
   - 融入用户提供的背景设定

4. **外在表现**（100-150字）：
   - 总部或主要据点位置
   - 标志性建筑或场所
   - 组织标志、徽章、制服等
   - 可辨识的外在特征

5. **组织目的/宗旨**：
   - 明确的组织目标
   - 长期愿景
   - 行动准则

6. **势力等级**：
   - 在世界中的影响力（0-100）
   - 综合实力评估

7. **所在地点**：
   - 主要活动区域
   - 势力范围

**重要格式要求：**
1. 只返回纯JSON格式，不要包含任何markdown标记、代码块标记或其他说明文字
2. 不要在JSON字符串值中使用中文引号（""''），改用【】或《》
3. 文本描述中的专有名词使用【】标记

请严格按照以下JSON格式返回：
{{
  "name": "组织名称",
  "is_organization": true,
  "organization_type": "组织类型",
  "personality": "组织特性（150-200字）",
  "background": "组织背景（200-300字）",
  "appearance": "外在表现（100-150字）",
  "organization_purpose": "组织目的和宗旨",
  "power_level": 75,
  "location": "所在地点",
  "motto": "组织格言或口号",
  "traits": ["特征1", "特征2", "特征3"],
  "color": "组织代表颜色（如：深红色、金色、黑色等）",
  "organization_members": ["重要成员1", "重要成员2", "重要成员3"]
}}

**组织设定要求：**
- 组织要符合项目的世界观和主题
- 目标和行动要合理，不能过于理想化或脸谱化
- 要有存在的必要性，能推动故事发展
- 内部要有层级和结构
- 与其他势力要有互动关系

**说明**：
1. power_level是0-100的整数，表示组织在世界中的影响力
2. organization_members是组织内重要成员的名字列表（如果已有角色，可以关联）
3. 所有文本描述要详细具体，避免空泛

再次强调：
1. 只返回纯JSON对象，不要有```json```这样的标记
2. 文本中不要使用中文引号（""），改用【】或《》
3. 不要有任何额外的文字说明"""

    @staticmethod
    def format_prompt(template: str, **kwargs) -> str:
        """
        格式化提示词模板
        
        Args:
            template: 提示词模板
            **kwargs: 模板参数
            
        Returns:
            格式化后的提示词
        """
        try:
            return template.format(**kwargs)
        except KeyError as e:
            raise ValueError(f"缺少必需的参数: {e}")
    
    @classmethod
    def get_denoising_prompt(cls, original_text: str, corpus_highlight_passages: str = "") -> str:
        """获取AI去味提示词"""
        prompt = cls.format_prompt(
            cls.AI_DENOISING,
            original_text=original_text
        )
        if corpus_highlight_passages:
            prompt += (
                "\n\n"
                f"{corpus_highlight_passages}\n\n"
                "请让上文的遣词节奏向参考范文靠拢，去 AI 味，"
                "但必须保留原文情节、人物关系与设定。"
            )
        return prompt
    
    @classmethod
    def get_world_building_prompt(cls, title: str, theme: str, genre: str = "") -> str:
        """获取世界构建提示词"""
        return cls.format_prompt(
            cls.WORLD_BUILDING,
            title=title,
            theme=theme,
            genre=genre or "通用类型"
        )
    
    @classmethod
    def get_characters_batch_prompt(cls, count: int, time_period: str, location: str,
                                   atmosphere: str, rules: str, theme: str,
                                   genre: str = "", requirements: str = "") -> str:
        """获取批量角色生成提示词"""
        return cls.format_prompt(
            cls.CHARACTERS_BATCH_GENERATION,
            count=count,
            time_period=time_period,
            location=location,
            atmosphere=atmosphere,
            rules=rules,
            theme=theme,
            genre=genre or "通用类型",
            requirements=requirements or "无特殊要求"
        )
    
    @classmethod
    def get_complete_outline_prompt(cls, title: str, theme: str, genre: str,
                                   chapter_count: int, narrative_perspective: str,
                                   target_words: int, time_period: str, location: str,
                                   atmosphere: str, rules: str, characters_info: str,
                                   requirements: str = "",
                                   mcp_references: str = "") -> str:
        """获取向导大纲生成提示词（支持MCP增强）"""
        # 格式化MCP参考资料
        mcp_text = ""
        if mcp_references:
            mcp_text = "【📚 MCP工具搜索 - 情节设计参考】\n"
            mcp_text += "以下是通过MCP工具搜索到的情节设计参考资料，可用于设计大纲结构和情节发展：\n\n"
            mcp_text += mcp_references
            mcp_text += "\n"
        
        return cls.format_prompt(
            cls.COMPLETE_OUTLINE_GENERATION,
            title=title,
            theme=theme,
            genre=genre,
            chapter_count=chapter_count,
            narrative_perspective=narrative_perspective,
            target_words=target_words,
            time_period=time_period,
            location=location,
            atmosphere=atmosphere,
            rules=rules,
            characters_info=characters_info,
            mcp_references=mcp_text,
            requirements=requirements or "无特殊要求"
        )
    
    @classmethod
    def get_chapter_generation_prompt(cls, title: str, theme: str, genre: str,
                                      narrative_perspective: str, time_period: str,
                                      location: str, atmosphere: str, rules: str,
                                      characters_info: str, outlines_context: str,
                                      chapter_number: int, chapter_title: str,
                                      chapter_outline: str, style_content: str = "",
                                      target_word_count: int = 3000,
                                      memory_context: dict = None,
                                      mcp_references: str = "",
                                      corpus_context: str = "") -> str:
        """
        获取章节完整创作提示词
        
        Args:
            style_content: 写作风格要求内容，如果提供则会追加到提示词中
            target_word_count: 目标字数，默认3000字
            memory_context: 记忆上下文（可选）
            mcp_references: MCP工具搜索的参考资料（可选）
        """
        # 计算最大字数（目标字数+1000）
        max_word_count = target_word_count + 1000
        
        # 格式化记忆上下文
        memory_text = ""
        if memory_context:
            memory_text = "\n【🧠 智能记忆系统 - 重要参考】\n"
            memory_text += memory_context.get('recent_context', '')
            memory_text += "\n" + memory_context.get('relevant_memories', '')
            memory_text += "\n" + memory_context.get('foreshadows', '')
            memory_text += "\n" + memory_context.get('character_states', '')
            memory_text += "\n" + memory_context.get('plot_points', '')
        
        # 格式化MCP参考资料
        mcp_text = ""
        if mcp_references:
            mcp_text = "\n【📚 MCP工具搜索 - 参考资料】\n"
            mcp_text += "以下是通过MCP工具搜索到的相关参考资料，可用于丰富情节和细节：\n\n"
            mcp_text += mcp_references
            mcp_text += "\n"
        if corpus_context:
            mcp_text += "\n" + corpus_context + "\n"
        
        base_prompt = cls.format_prompt(
            cls.CHAPTER_GENERATION,
            title=title,
            theme=theme,
            genre=genre,
            narrative_perspective=narrative_perspective,
            time_period=time_period,
            location=location,
            atmosphere=atmosphere,
            rules=rules,
            characters_info=characters_info,
            outlines_context=outlines_context,
            chapter_number=chapter_number,
            chapter_title=chapter_title,
            chapter_outline=chapter_outline,
            target_word_count=target_word_count,
            max_word_count=max_word_count
        )
        
        # 插入记忆上下文和MCP参考资料
        insert_text = ""
        if memory_text:
            insert_text += memory_text
        if mcp_text:
            insert_text += mcp_text
        
        if insert_text:
            base_prompt = base_prompt.replace(
                "本章信息：",
                insert_text + "\n\n本章信息："
            )
        
        # 如果有风格要求，应用到提示词中
        if style_content:
            return WritingStyleManager.apply_style_to_prompt(base_prompt, style_content)
        
        return base_prompt
    
    @classmethod
    def get_chapter_generation_with_context_prompt(cls, title: str, theme: str, genre: str,
                                                   narrative_perspective: str, time_period: str,
                                                   location: str, atmosphere: str, rules: str,
                                                   characters_info: str, outlines_context: str,
                                                   previous_content: str, chapter_number: int,
                                                   chapter_title: str, chapter_outline: str,
                                                   style_content: str = "",
                                                   target_word_count: int = 3000,
                                                   memory_context: dict = None,
                                                   mcp_references: str = "",
                                                   corpus_context: str = "") -> str:
        """
        获取章节完整创作提示词（带前置章节上下文和记忆增强）
        
        Args:
            style_content: 写作风格要求内容，如果提供则会追加到提示词中
            target_word_count: 目标字数，默认3000字
            memory_context: 记忆上下文（可选）
            mcp_references: MCP工具搜索的参考资料（可选）
        """
        # 计算最大字数（目标字数+1000）
        max_word_count = target_word_count + 1000
        
        # 格式化记忆上下文
        memory_text = ""
        if memory_context:
            memory_text = memory_context.get('recent_context', '')
            memory_text += "\n" + memory_context.get('relevant_memories', '')
            memory_text += "\n" + memory_context.get('foreshadows', '')
            memory_text += "\n" + memory_context.get('character_states', '')
            memory_text += "\n" + memory_context.get('plot_points', '')
        else:
            memory_text = "暂无相关记忆"
        
        # 格式化MCP参考资料
        if mcp_references:
            memory_text += "\n\n【📚 MCP工具搜索 - 参考资料】\n"
            memory_text += "以下是通过MCP工具搜索到的相关参考资料，可用于丰富情节和细节：\n\n"
            memory_text += mcp_references
        if corpus_context:
            memory_text += "\n\n" + corpus_context
        
        base_prompt = cls.format_prompt(
            cls.CHAPTER_GENERATION_WITH_CONTEXT,
            title=title,
            theme=theme,
            genre=genre,
            narrative_perspective=narrative_perspective,
            time_period=time_period,
            location=location,
            atmosphere=atmosphere,
            rules=rules,
            characters_info=characters_info,
            outlines_context=outlines_context,
            previous_content=previous_content,
            chapter_number=chapter_number,
            chapter_title=chapter_title,
            chapter_outline=chapter_outline,
            target_word_count=target_word_count,
            max_word_count=max_word_count,
            memory_context=memory_text
        )
        
        # 如果有风格要求，应用到提示词中
        if style_content:
            return WritingStyleManager.apply_style_to_prompt(base_prompt, style_content)
        
        return base_prompt
    
    @classmethod
    def get_outline_prompt(cls, genre: str, theme: str, target_words: int,
                          requirements: str = "") -> str:
        """获取大纲生成提示词"""
        return cls.format_prompt(
            cls.OUTLINE_GENERATION,
            genre=genre,
            theme=theme,
            target_words=target_words,
            requirements=requirements or "无特殊要求"
        )
    
    @classmethod
    def get_outline_continue_prompt(cls, title: str, theme: str, genre: str,
                                    narrative_perspective: str, chapter_count: int,
                                    time_period: str, location: str, atmosphere: str,
                                    rules: str, characters_info: str,
                                    current_chapter_count: int, all_chapters_brief: str,
                                    recent_plot: str, plot_stage_instruction: str,
                                    start_chapter: int, story_direction: str,
                                    requirements: str = "",
                                    memory_context: dict = None,
                                    mcp_references: str = "") -> str:
        """获取大纲续写提示词（支持记忆+MCP增强）"""
        end_chapter = start_chapter + chapter_count - 1
        
        # 格式化记忆上下文
        memory_text = ""
        if memory_context:
            memory_text = memory_context.get('recent_context', '')
            memory_text += "\n" + memory_context.get('relevant_memories', '')
            memory_text += "\n" + memory_context.get('foreshadows', '')
            memory_text += "\n" + memory_context.get('character_states', '')
            memory_text += "\n" + memory_context.get('plot_points', '')
        else:
            memory_text = "暂无相关记忆（可能是首次续写或记忆库为空）"
        
        # 格式化MCP参考资料
        mcp_text = ""
        if mcp_references:
            mcp_text = "\n\n【📚 MCP工具搜索 - 续写参考资料】\n"
            mcp_text += "以下是通过MCP工具搜索到的续写参考资料，可用于丰富情节发展和冲突设计：\n\n"
            mcp_text += mcp_references
            mcp_text += "\n"
        
        return cls.format_prompt(
            cls.OUTLINE_CONTINUE_GENERATION,
            title=title,
            theme=theme,
            genre=genre,
            narrative_perspective=narrative_perspective,
            chapter_count=chapter_count,
            time_period=time_period,
            location=location,
            atmosphere=atmosphere,
            rules=rules,
            characters_info=characters_info,
            current_chapter_count=current_chapter_count,
            all_chapters_brief=all_chapters_brief,
            recent_plot=recent_plot,
            plot_stage_instruction=plot_stage_instruction,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            story_direction=story_direction,
            requirements=requirements or "无特殊要求",
            memory_context=memory_text,
            mcp_references=mcp_text
        )
    
    @classmethod
    def get_single_character_prompt(cls, project_context: str, user_input: str) -> str:
        """获取单个角色生成提示词"""
        return cls.format_prompt(
            cls.SINGLE_CHARACTER_GENERATION,
            project_context=project_context,
            user_input=user_input
        )
    
    @classmethod
    def get_single_organization_prompt(cls, project_context: str, user_input: str) -> str:
        """获取单个组织生成提示词"""
        return cls.format_prompt(
            cls.SINGLE_ORGANIZATION_GENERATION,
            project_context=project_context,
            user_input=user_input
        )


# 创建全局提示词服务实例
prompt_service = PromptService()
