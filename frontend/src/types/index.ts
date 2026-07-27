// 用户类型定义
export interface User {
  user_id: string;
  username: string;
  display_name: string;
  avatar_url?: string;
  trust_level: number;
  is_admin: boolean;
  linuxdo_id: string;
  created_at: string;
  last_login: string;
}

// 设置类型定义
export interface Settings {
  id: string;
  user_id: string;
  api_provider: string;
  api_key: string;
  api_base_url: string;
  llm_model: string;
  temperature: number;
  max_tokens: number;
  preferences?: string;
  created_at: string;
  updated_at: string;
}

export interface SettingsUpdate {
  api_provider?: string;
  api_key?: string;
  api_base_url?: string;
  llm_model?: string;
  temperature?: number;
  max_tokens?: number;
  preferences?: string;
}

// LinuxDO 授权 URL 响应
export interface AuthUrlResponse {
  auth_url: string;
  state: string;
}

// 项目类型定义
export interface Project {
  id: string;  // UUID字符串
  title: string;
  description?: string;
  theme?: string;
  genre?: string;
  target_words?: number;
  current_words: number;
  status: 'planning' | 'writing' | 'revising' | 'completed';
  wizard_status?: 'incomplete' | 'completed';
  wizard_step?: number;
  world_time_period?: string;
  world_location?: string;
  world_atmosphere?: string;
  world_rules?: string;
  chapter_count?: number;
  narrative_perspective?: string;
  character_count?: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  title: string;
  description?: string;
  theme?: string;
  genre?: string;
  target_words?: number;
  wizard_status?: 'incomplete' | 'completed';
  wizard_step?: number;
  world_time_period?: string;
  world_location?: string;
  world_atmosphere?: string;
  world_rules?: string;
}

export interface ProjectUpdate {
  title?: string;
  description?: string;
  theme?: string;
  genre?: string;
  target_words?: number;
  status?: 'planning' | 'writing' | 'revising' | 'completed';
  world_time_period?: string;
  world_location?: string;
  world_atmosphere?: string;
  world_rules?: string;
  chapter_count?: number;
  narrative_perspective?: string;
  character_count?: number;
  // current_words 由章节内容自动计算，不在此接口中
}

// 向导专用的项目更新接口，包含向导流程控制字段
export interface ProjectWizardUpdate extends ProjectUpdate {
  wizard_status?: 'incomplete' | 'completed';
  wizard_step?: number;
}

// 项目创建向导
export interface ProjectWizardRequest {
  title: string;
  theme: string;
  genre?: string;
  chapter_count: number;
  narrative_perspective: string;
  character_count?: number;
  target_words?: number;
  world_building?: {
    time_period: string;
    location: string;
    atmosphere: string;
    rules: string;
  };
}

export interface WorldBuildingResponse {
  project_id: string;
  time_period: string;
  location: string;
  atmosphere: string;
  rules: string;
}

// 大纲类型定义
export interface Outline {
  id: string;
  project_id: string;
  title: string;
  content: string;
  structure?: string;
  order_index: number;
  created_at: string;
  updated_at: string;
}

export interface OutlineCreate {
  project_id: string;
  title: string;
  content: string;
  structure?: string;
  order_index: number;
}

export interface OutlineUpdate {
  title?: string;
  content?: string;
  // structure 暂不支持修改
  // order_index 只能通过 reorder 接口批量调整
}

// 角色类型定义
export interface Character {
  id: string;
  project_id: string;
  name: string;
  age?: string;
  gender?: string;
  is_organization: boolean;
  role_type?: string;
  personality?: string;
  background?: string;
  appearance?: string;
  relationships?: string;
  organization_type?: string;
  organization_purpose?: string;
  organization_members?: string;
  traits?: string;
  avatar_url?: string;
  // 组织扩展字段（从Organization表关联）
  power_level?: number;
  location?: string;
  motto?: string;
  color?: string;
  created_at: string;
  updated_at: string;
}

export interface CharacterUpdate {
  name?: string;
  age?: string;
  gender?: string;
  is_organization?: boolean;
  role_type?: string;
  personality?: string;
  background?: string;
  appearance?: string;
  relationships?: string;
  organization_type?: string;
  organization_purpose?: string;
  organization_members?: string;
  traits?: string;
  // 组织扩展字段
  power_level?: number;
  location?: string;
  motto?: string;
  color?: string;
}

// 章节类型定义
export interface Chapter {
  id: string;
  project_id: string;
  title: string;
  content?: string;
  summary?: string;
  chapter_number: number;
  word_count: number;
  status: 'draft' | 'writing' | 'completed';
  created_at: string;
  updated_at: string;
}

export interface ChapterCreate {
  project_id: string;
  title: string;
  chapter_number: number;
  content?: string;
  summary?: string;
  status?: 'draft' | 'writing' | 'completed';
}

export interface ChapterUpdate {
  title?: string;
  content?: string;
  // chapter_number 不允许修改，由大纲顺序决定
  summary?: string;
  // word_count 自动计算，不允许手动修改
  status?: 'draft' | 'writing' | 'completed';
}

// 章节生成请求类型
export interface ChapterGenerateRequest {
  style_id?: number;
  target_word_count?: number;
  one_time_prompt?: string;
}

// 章节生成检查响应
export interface ChapterCanGenerateResponse {
  can_generate: boolean;
  reason: string;
  previous_chapters: {
    id: string;
    chapter_number: number;
    title: string;
    has_content: boolean;
    word_count: number;
  }[];
  chapter_number: number;
}

// AI生成请求类型
export interface GenerateOutlineRequest {
  project_id: string;
  genre?: string;
  theme: string;
  chapter_count: number;
  narrative_perspective: string;
  world_context?: Record<string, unknown>;
  characters_context?: Character[];
  target_words?: number;
  requirements?: string;
  provider?: string;
  model?: string;
  // 续写功能新增字段
  mode?: 'auto' | 'new' | 'continue';
  story_direction?: string;
  plot_stage?: 'development' | 'climax' | 'ending';
  keep_existing?: boolean;
}

// 大纲重排序请求类型
export interface OutlineReorderItem {
  id: string;
  order_index: number;
}

export interface OutlineReorderRequest {
  orders: OutlineReorderItem[];
}

export interface GenerateCharacterRequest {
  project_id: string;
  name?: string;
  role_type?: string;
  background?: string;
  requirements?: string;
  provider?: string;
  model?: string;
}

export interface PolishTextRequest {
  text: string;
  style?: string;
}

// 向导API响应类型
export interface GenerateCharactersResponse {
  characters: Character[];
}

export interface GenerateOutlineResponse {
  outlines: Outline[];
}

// API响应类型
export interface ApiResponse<T> {
  data: T;
  message?: string;
}

// 写作风格类型定义
export interface WritingStyle {
  id: number;
  project_id: string;
  name: string;
  style_type: 'preset' | 'custom';
  preset_id?: string;
  description?: string;
  prompt_content: string;
  is_default: boolean;
  order_index: number;
  created_at: string;
  updated_at: string;
}

export interface WritingStyleCreate {
  project_id: string;
  name: string;
  style_type: 'preset' | 'custom';
  preset_id?: string;
  description?: string;
  prompt_content: string;
  is_default?: boolean;
}

export interface WritingStyleUpdate {
  name?: string;
  description?: string;
  prompt_content?: string;
  order_index?: number;
}

export interface PresetStyle {
  id: string;
  name: string;
  description: string;
  prompt_content: string;
}

export interface WritingStyleListResponse {
  styles: WritingStyle[];
  total: number;
}

export interface PaginationResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// 向导表单数据类型
export interface WizardBasicInfo {
  title: string;
  description: string;
  theme: string;
  genre: string | string[];
  chapter_count: number;
  narrative_perspective: string;
  character_count?: number;
  target_words?: number;
}

// API 错误响应类型
export interface ApiError {
  response?: {
    data?: {
      detail?: string;
    };
  };
  message?: string;
}

// 章节分析任务相关类型
export interface AnalysisTask {
  has_task: boolean;
  task_id: string | null;
  chapter_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'none';
  progress: number;
  error_message?: string | null;
  auto_recovered?: boolean;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

// 分析结果 - 钩子
export interface AnalysisHook {
  type: string;
  content: string;
  strength: number;
  position: string;
}

// 分析结果 - 伏笔
export interface AnalysisForeshadow {
  content: string;
  type: 'planted' | 'resolved';
  strength: number;
  subtlety: number;
  reference_chapter?: number;
}

// 分析结果 - 冲突
export interface AnalysisConflict {
  types: string[];
  parties: string[];
  level: number;
  description: string;
  resolution_progress: number;
}

// 分析结果 - 情感曲线
export interface AnalysisEmotionalArc {
  primary_emotion: string;
  intensity: number;
  curve: string;
  secondary_emotions: string[];
}

// 分析结果 - 角色状态
export interface AnalysisCharacterState {
  character_name: string;
  state_before: string;
  state_after: string;
  psychological_change: string;
  key_event: string;
  relationship_changes: Record<string, string>;
}

// 分析结果 - 情节点
export interface AnalysisPlotPoint {
  content: string;
  type: 'revelation' | 'conflict' | 'resolution' | 'transition';
  importance: number;
  impact: string;
}

// 分析结果 - 场景
export interface AnalysisScene {
  location: string;
  atmosphere: string;
  duration: string;
}

// 分析结果 - 评分
export interface AnalysisScores {
  pacing: number;
  engagement: number;
  coherence: number;
  overall: number;
}

// 完整分析数据 - 匹配后端PlotAnalysis模型
export interface AnalysisData {
  id: string;
  chapter_id: string;
  plot_stage: string;
  conflict_level: number;
  conflict_types: string[];
  emotional_tone: string;
  emotional_intensity: number;
  hooks: AnalysisHook[];
  hooks_count: number;
  foreshadows: AnalysisForeshadow[];
  foreshadows_planted: number;
  foreshadows_resolved: number;
  plot_points: AnalysisPlotPoint[];
  plot_points_count: number;
  character_states: AnalysisCharacterState[];
  scenes?: AnalysisScene[];
  pacing: string;
  overall_quality_score: number;
  pacing_score: number;
  engagement_score: number;
  coherence_score: number;
  analysis_report: string;
  suggestions: string[];
  dialogue_ratio: number;
  description_ratio: number;
  created_at: string;
}

// 记忆片段
export interface StoryMemory {
  id: string;
  type: 'hook' | 'foreshadow' | 'plot_point' | 'character_event';
  title: string;
  content: string;
  importance: number;
  tags: string[];
  is_foreshadow: 0 | 1 | 2; // 0=普通, 1=已埋下, 2=已回收
}

// 章节分析结果响应 - 匹配后端API返回
export interface ChapterAnalysisResponse {
  chapter_id: string;
  analysis: AnalysisData;  // 注意：后端返回的是analysis而不是analysis_data
  memories: StoryMemory[];
  created_at: string;
}

// 手动触发分析响应
export interface TriggerAnalysisResponse {
  task_id: string;
  chapter_id: string;
  status: string;
  message: string;
}

// MCP 插件类型定义 - 优化后只包含必要字段
export interface MCPPlugin {
  id: string;
  plugin_name: string;
  display_name: string;
  description?: string;
  plugin_type: 'http' | 'stdio';
  category: string;
  
  // HTTP类型字段
  server_url?: string;
  headers?: Record<string, string>;
  
  // Stdio类型字段
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  
  // 状态字段
  enabled: boolean;
  status: 'active' | 'inactive' | 'error';
  last_error?: string;
  last_test_at?: string;
  
  // 时间戳
  created_at: string;
}

export interface MCPPluginCreate {
  plugin_name: string;
  display_name?: string;
  description?: string;
  server_type: 'http' | 'stdio';
  server_url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  enabled?: boolean;
}

export interface MCPPluginUpdate {
  display_name?: string;
  description?: string;
  server_url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  enabled?: boolean;
}

export interface MCPTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export interface MCPTestResult {
  success: boolean;
  message: string;
  tools?: MCPTool[];
  tools_count?: number;
  response_time_ms?: number;
  error?: string;
  error_type?: string;
  suggestions?: string[];
}

export interface MCPToolCallRequest {
  plugin_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
}

export interface MCPToolCallResponse {
  success: boolean;
  result?: unknown;
  error?: string;
}
