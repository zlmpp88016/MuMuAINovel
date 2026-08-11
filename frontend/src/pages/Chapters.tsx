import { useState, useEffect, useRef } from 'react';
import { List, Button, Modal, Form, Input, Select, message, Empty, Space, Badge, Tag, Card, Tooltip, InputNumber, Progress, Alert, Radio } from 'antd';
import { EditOutlined, FileTextOutlined, ThunderboltOutlined, LockOutlined, DownloadOutlined, SettingOutlined, FundOutlined, SyncOutlined, CheckCircleOutlined, CloseCircleOutlined, RocketOutlined, StopOutlined } from '@ant-design/icons';
import { useStore } from '../store';
import { useChapterSync } from '../store/hooks';
import { projectApi, writingStyleApi } from '../services/api';
import type { Chapter, ChapterUpdate, ApiError, WritingStyle, AnalysisTask } from '../types';
import { cardStyles } from '../components/CardStyles';
import ChapterAnalysis from '../components/ChapterAnalysis';
import { SSELoadingOverlay } from '../components/SSELoadingOverlay';
import LLMConfigSelector from '../components/LLMConfigSelector';

const { TextArea } = Input;

export default function Chapters() {
  const { currentProject, chapters, setCurrentChapter, setCurrentProject } = useStore();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [isContinuing, setIsContinuing] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form] = Form.useForm();
  const [editorForm] = Form.useForm();
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);
  const contentTextAreaRef = useRef<any>(null);
  const [writingStyles, setWritingStyles] = useState<WritingStyle[]>([]);
  const [selectedStyleId, setSelectedStyleId] = useState<number | undefined>();
  const [targetWordCount, setTargetWordCount] = useState<number>(3000);
  const [llmConfigId, setLLMConfigId] = useState<string | undefined>();
  const [analysisVisible, setAnalysisVisible] = useState(false);
  const [analysisChapterId, setAnalysisChapterId] = useState<string | null>(null);
  // 分析任务状态管理
  const [analysisTasksMap, setAnalysisTasksMap] = useState<Record<string, AnalysisTask>>({});
  const pollingIntervalsRef = useRef<Record<string, number>>({});
  
  // 单章节生成进度状态
  const [singleChapterProgress, setSingleChapterProgress] = useState(0);
  const [singleChapterProgressMessage, setSingleChapterProgressMessage] = useState('');
  
  // 批量生成相关状态
  const [batchGenerateVisible, setBatchGenerateVisible] = useState(false);
  const [batchGenerating, setBatchGenerating] = useState(false);
  const [batchTaskId, setBatchTaskId] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<{
    status: string;
    total: number;
    completed: number;
    current_chapter_number: number | null;
    estimated_time_minutes?: number;
  } | null>(null);
  const batchPollingIntervalRef = useRef<number | null>(null);

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth <= 768);
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const {
    refreshChapters,
    updateChapter,
    generateChapterContentStream
  } = useChapterSync();

  useEffect(() => {
    if (currentProject?.id) {
      refreshChapters();
      loadWritingStyles();
      loadAnalysisTasks();
      checkAndRestoreBatchTask();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentProject?.id]);

  // 清理轮询定时器
  useEffect(() => {
    return () => {
      Object.values(pollingIntervalsRef.current).forEach(interval => {
        clearInterval(interval);
      });
      if (batchPollingIntervalRef.current) {
        clearInterval(batchPollingIntervalRef.current);
      }
    };
  }, []);

  // 加载所有章节的分析任务状态
  const loadAnalysisTasks = async () => {
    if (!chapters || chapters.length === 0) return;
    
    const tasksMap: Record<string, AnalysisTask> = {};
    
    for (const chapter of chapters) {
      // 只查询有内容的章节
      if (chapter.content && chapter.content.trim() !== '') {
        try {
          const response = await fetch(`/api/chapters/${chapter.id}/analysis/status`);
          if (response.ok) {
            const task: AnalysisTask = await response.json();
            tasksMap[chapter.id] = task;
            
            // 如果任务正在运行，启动轮询
            if (task.status === 'pending' || task.status === 'running') {
              startPollingTask(chapter.id);
            }
          }
        } catch (error) {
          // 404或其他错误表示没有分析任务，忽略
          console.debug(`章节 ${chapter.id} 暂无分析任务`);
        }
      }
    }
    
    setAnalysisTasksMap(tasksMap);
  };

  // 启动单个章节的任务轮询
  const startPollingTask = (chapterId: string) => {
    // 如果已经在轮询，先清除
    if (pollingIntervalsRef.current[chapterId]) {
      clearInterval(pollingIntervalsRef.current[chapterId]);
    }
    
    const interval = window.setInterval(async () => {
      try {
        const response = await fetch(`/api/chapters/${chapterId}/analysis/status`);
        if (!response.ok) return;
        
        const task: AnalysisTask = await response.json();
        
        setAnalysisTasksMap(prev => ({
          ...prev,
          [chapterId]: task
        }));
        
        // 任务完成或失败，停止轮询
        if (task.status === 'completed' || task.status === 'failed') {
          clearInterval(pollingIntervalsRef.current[chapterId]);
          delete pollingIntervalsRef.current[chapterId];
          
          if (task.status === 'completed') {
            message.success(`章节分析完成`);
          } else if (task.status === 'failed') {
            message.error(`章节分析失败: ${task.error_message || '未知错误'}`);
          }
        }
      } catch (error) {
        console.error('轮询分析任务失败:', error);
      }
    }, 2000);
    
    pollingIntervalsRef.current[chapterId] = interval;
    
    // 5分钟超时
    setTimeout(() => {
      if (pollingIntervalsRef.current[chapterId]) {
        clearInterval(pollingIntervalsRef.current[chapterId]);
        delete pollingIntervalsRef.current[chapterId];
      }
    }, 300000);
  };

  const loadWritingStyles = async () => {
    if (!currentProject?.id) return;
    
    try {
      const response = await writingStyleApi.getProjectStyles(currentProject.id);
      setWritingStyles(response.styles);
      
      // 设置默认风格为初始选中
      const defaultStyle = response.styles.find(s => s.is_default);
      if (defaultStyle) {
        setSelectedStyleId(defaultStyle.id);
      }
    } catch (error) {
      console.error('加载写作风格失败:', error);
      message.error('加载写作风格失败');
    }
  };

  // 检查并恢复批量生成任务
  const checkAndRestoreBatchTask = async () => {
    if (!currentProject?.id) return;
    
    try {
      const response = await fetch(`/api/chapters/project/${currentProject.id}/batch-generate/active`);
      if (!response.ok) return;
      
      const data = await response.json();
      
      if (data.has_active_task && data.task) {
        const task = data.task;
        
        // 恢复任务状态
        setBatchTaskId(task.batch_id);
        setBatchProgress({
          status: task.status,
          total: task.total,
          completed: task.completed,
          current_chapter_number: task.current_chapter_number,
        });
        setBatchGenerating(true);
        setBatchGenerateVisible(true);
        
        // 启动轮询
        startBatchPolling(task.batch_id);
        
        message.info('检测到未完成的批量生成任务，已自动恢复');
      }
    } catch (error) {
      console.error('检查批量生成任务失败:', error);
    }
  };

  if (!currentProject) return null;

  const canGenerateChapter = (chapter: Chapter): boolean => {
    if (chapter.chapter_number === 1) {
      return true;
    }
    
    const previousChapters = chapters.filter(
      c => c.chapter_number < chapter.chapter_number
    );
    
    return previousChapters.every(c => c.content && c.content.trim() !== '');
  };

  const getGenerateDisabledReason = (chapter: Chapter): string => {
    if (chapter.chapter_number === 1) {
      return '';
    }
    
    const previousChapters = chapters.filter(
      c => c.chapter_number < chapter.chapter_number
    );
    
    const incompleteChapters = previousChapters.filter(
      c => !c.content || c.content.trim() === ''
    );
    
    if (incompleteChapters.length > 0) {
      const numbers = incompleteChapters.map(c => c.chapter_number).join('、');
      return `需要先完成前置章节：第 ${numbers} 章`;
    }
    
    return '';
  };

  const handleOpenModal = (id: string) => {
    const chapter = chapters.find(c => c.id === id);
    if (chapter) {
      form.setFieldsValue(chapter);
      setEditingId(id);
      setIsModalOpen(true);
    }
  };

  const handleSubmit = async (values: ChapterUpdate) => {
    if (!editingId) return;
    
    try {
      await updateChapter(editingId, values);
      message.success('章节更新成功');
      setIsModalOpen(false);
      form.resetFields();
    } catch {
      message.error('操作失败');
    }
  };

  const handleOpenEditor = (id: string) => {
    const chapter = chapters.find(c => c.id === id);
    if (chapter) {
      setCurrentChapter(chapter);
      editorForm.setFieldsValue({
        title: chapter.title,
        content: chapter.content,
      });
      setEditingId(id);
      setIsEditorOpen(true);
    }
  };

  const handleEditorSubmit = async (values: ChapterUpdate) => {
    if (!editingId || !currentProject) return;
    
    try {
      await updateChapter(editingId, values);
      
      // 刷新项目信息以更新总字数统计
      const updatedProject = await projectApi.getProject(currentProject.id);
      setCurrentProject(updatedProject);
      
      message.success('章节保存成功');
      setIsEditorOpen(false);
    } catch {
      message.error('保存失败');
    }
  };

  const handleGenerate = async (oneTimePrompt?: string) => {
    if (!editingId) return;

    try {
      setIsContinuing(true);
      setIsGenerating(true);
      setSingleChapterProgress(0);
      setSingleChapterProgressMessage('准备开始生成...');
      
      const result = await generateChapterContentStream(
        editingId,
        (content) => {
          editorForm.setFieldsValue({ content });
          
          if (contentTextAreaRef.current) {
            const textArea = contentTextAreaRef.current.resizableTextArea?.textArea;
            if (textArea) {
              textArea.scrollTop = textArea.scrollHeight;
            }
          }
        },
        selectedStyleId,
        targetWordCount,
        (progressMsg, progressValue) => {
          // 进度回调
          setSingleChapterProgress(progressValue);
          setSingleChapterProgressMessage(progressMsg);
        },
        oneTimePrompt,
        llmConfigId
      );
      
      message.success('AI创作成功，正在分析章节内容...');
      
      // 如果返回了分析任务ID，启动轮询
      if (result?.analysis_task_id) {
        const taskId = result.analysis_task_id;
        setAnalysisTasksMap(prev => ({
          ...prev,
          [editingId]: {
            has_task: true,
            task_id: taskId,
            chapter_id: editingId,
            status: 'pending',
            progress: 0
          }
        }));
        
        // 启动轮询
        startPollingTask(editingId);
      }
    } catch (error) {
      const apiError = error as ApiError;
      message.error('AI创作失败：' + (apiError.response?.data?.detail || apiError.message || '未知错误'));
    } finally {
      setIsContinuing(false);
      setIsGenerating(false);
      setSingleChapterProgress(0);
      setSingleChapterProgressMessage('');
    }
  };

  const showGenerateModal = (chapter: Chapter) => {
    const previousChapters = chapters.filter(
      c => c.chapter_number < chapter.chapter_number
    ).sort((a, b) => a.chapter_number - b.chapter_number);

    const selectedStyle = writingStyles.find(s => s.id === selectedStyleId);
    let oneTimePrompt = '';

    const modal = Modal.confirm({
      title: 'AI创作章节内容',
      width: 700,
      centered: true,
      content: (
        <div style={{ marginTop: 16 }}>
          <p>AI将根据以下信息创作本章内容：</p>
          <ul>
            <li>章节大纲和要求</li>
            <li>项目的世界观设定</li>
            <li>相关角色信息</li>
            <li><strong>前面已完成章节的内容（确保剧情连贯）</strong></li>
            {selectedStyle && (
              <li><strong>写作风格：{selectedStyle.name}</strong></li>
            )}
            <li><strong>目标字数：{targetWordCount}字</strong></li>
          </ul>

          <div style={{ marginTop: 16 }}>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>
              本次自定义 Prompt（可选）
            </div>
            <TextArea
              autoSize={{ minRows: 3, maxRows: 8 }}
              maxLength={10000}
              showCount
              placeholder="例如：本章增加悬疑氛围，结尾保留一个未揭晓的线索。留空则按原有方式生成。"
              onChange={(event) => {
                oneTimePrompt = event.target.value;
              }}
            />
            <div style={{ marginTop: 6, color: '#666', fontSize: 12 }}>
              该 Prompt 仅用于此次生成，关闭弹框后不会保存，也不会影响后续章节。
            </div>
          </div>
          
          {previousChapters.length > 0 && (
            <div style={{
              marginTop: 16,
              padding: 12,
              background: '#f0f5ff',
              borderRadius: 4,
              border: '1px solid #adc6ff'
            }}>
              <div style={{ marginBottom: 8, fontWeight: 500, color: '#1890ff' }}>
                📚 将引用的前置章节（共{previousChapters.length}章）：
              </div>
              <div style={{ maxHeight: 150, overflowY: 'auto' }}>
                {previousChapters.map(ch => (
                  <div key={ch.id} style={{ padding: '4px 0', fontSize: 13 }}>
                    ✓ 第{ch.chapter_number}章：{ch.title} ({ch.word_count || 0}字)
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 8, fontSize: 12, color: '#666' }}>
                💡 AI会参考这些章节内容，确保情节连贯、角色状态一致
              </div>
            </div>
          )}
          
          <p style={{ color: '#ff4d4f', marginTop: 16, marginBottom: 0 }}>
            ⚠️ 注意：此操作将覆盖当前章节内容
          </p>
        </div>
      ),
      okText: '开始创作',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        modal.update({
          okButtonProps: { danger: true, loading: true },
          cancelButtonProps: { disabled: true },
          closable: false,
          maskClosable: false,
          keyboard: false,
        });
        
        try {
          if (!selectedStyleId) {
            message.error('请先选择写作风格');
            modal.update({
              okButtonProps: { danger: true, loading: false },
              cancelButtonProps: { disabled: false },
              closable: true,
              maskClosable: true,
              keyboard: true,
            });
            return;
          }
          await handleGenerate(oneTimePrompt);
          modal.destroy();
        } catch (error) {
          modal.update({
            okButtonProps: { danger: true, loading: false },
            cancelButtonProps: { disabled: false },
            closable: true,
            maskClosable: true,
            keyboard: true,
          });
        }
      },
      onCancel: () => {
        if (isGenerating) {
          message.warning('AI正在创作中，请等待完成');
          return false;
        }
      },
    });
  };

  const getStatusColor = (status: string) => {
    const colors: Record<string, string> = {
      'draft': 'default',
      'writing': 'processing',
      'completed': 'success',
    };
    return colors[status] || 'default';
  };

  const getStatusText = (status: string) => {
    const texts: Record<string, string> = {
      'draft': '草稿',
      'writing': '创作中',
      'completed': '已完成',
    };
    return texts[status] || status;
  };

  const sortedChapters = [...chapters].sort((a, b) => a.chapter_number - b.chapter_number);

  const handleExport = () => {
    if (chapters.length === 0) {
      message.warning('当前项目没有章节，无法导出');
      return;
    }
    
    Modal.confirm({
      title: '导出项目章节',
      content: `确定要将《${currentProject.title}》的所有章节导出为TXT文件吗？`,
      centered: true,
      okText: '确定导出',
      cancelText: '取消',
      onOk: () => {
        try {
          projectApi.exportProject(currentProject.id);
          message.success('开始下载导出文件');
        } catch {
          message.error('导出失败，请重试');
        }
      },
    });
  };

  const handleShowAnalysis = (chapterId: string) => {
    setAnalysisChapterId(chapterId);
    setAnalysisVisible(true);
  };

  // 批量生成函数
  const handleBatchGenerate = async (values: {
    startChapterNumber: number;
    count: number;
    enableAnalysis: boolean;
    styleId?: number;
    targetWordCount?: number;
    llmConfigId?: string;
  }) => {
    if (!currentProject?.id) return;
    
    // 使用批量生成对话框中选择的风格和字数，如果没有选择则使用默认值
    const styleId = values.styleId || selectedStyleId;
    const wordCount = values.targetWordCount || targetWordCount;
    
    if (!styleId) {
      message.error('请选择写作风格');
      return;
    }
    
    try {
      setBatchGenerating(true);
      
      const response = await fetch(`/api/chapters/project/${currentProject.id}/batch-generate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          start_chapter_number: values.startChapterNumber,
          count: values.count,
          enable_analysis: values.enableAnalysis,
          style_id: styleId,
          target_word_count: wordCount,
          llm_config_id: values.llmConfigId || llmConfigId,
        }),
      });
      
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '创建批量生成任务失败');
      }
      
      const result = await response.json();
      setBatchTaskId(result.batch_id);
      setBatchProgress({
        status: 'running',
        total: result.chapters_to_generate.length,
        completed: 0,
        current_chapter_number: values.startChapterNumber,
        estimated_time_minutes: result.estimated_time_minutes,
      });
      
      message.success(`批量生成任务已创建，预计需要 ${result.estimated_time_minutes} 分钟`);
      
      // 开始轮询任务状态
      startBatchPolling(result.batch_id);
      
    } catch (error: any) {
      message.error('创建批量生成任务失败：' + (error.message || '未知错误'));
      setBatchGenerating(false);
      setBatchGenerateVisible(false);
    }
  };

  // 轮询批量生成任务状态
  const startBatchPolling = (taskId: string) => {
    if (batchPollingIntervalRef.current) {
      clearInterval(batchPollingIntervalRef.current);
    }
    
    const poll = async () => {
      try {
        const response = await fetch(`/api/chapters/batch-generate/${taskId}/status`);
        if (!response.ok) return;
        
        const status = await response.json();
        setBatchProgress({
          status: status.status,
          total: status.total,
          completed: status.completed,
          current_chapter_number: status.current_chapter_number,
        });
        
        // 任务完成或失败，停止轮询
        if (status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled') {
          if (batchPollingIntervalRef.current) {
            clearInterval(batchPollingIntervalRef.current);
            batchPollingIntervalRef.current = null;
          }
          
          setBatchGenerating(false);
          
          if (status.status === 'completed') {
            message.success(`批量生成完成！成功生成 ${status.completed} 章`);
            // 刷新章节列表
            refreshChapters();
            loadAnalysisTasks();
          } else if (status.status === 'failed') {
            message.error(`批量生成失败：${status.error_message || '未知错误'}`);
          } else if (status.status === 'cancelled') {
            message.warning('批量生成已取消');
          }
          
          // 延迟关闭对话框，让用户看到最终状态
          setTimeout(() => {
            setBatchGenerateVisible(false);
            setBatchTaskId(null);
            setBatchProgress(null);
          }, 2000);
        }
      } catch (error) {
        console.error('轮询批量生成状态失败:', error);
      }
    };
    
    // 立即执行一次
    poll();
    
    // 每2秒轮询一次
    batchPollingIntervalRef.current = window.setInterval(poll, 2000);
  };

  // 取消批量生成
  const handleCancelBatchGenerate = async () => {
    if (!batchTaskId) return;
    
    try {
      const response = await fetch(`/api/chapters/batch-generate/${batchTaskId}/cancel`, {
        method: 'POST',
      });
      
      if (!response.ok) {
        throw new Error('取消失败');
      }
      
      message.success('批量生成已取消');
    } catch (error: any) {
      message.error('取消失败：' + (error.message || '未知错误'));
    }
  };

  // 打开批量生成对话框
  const handleOpenBatchGenerate = () => {
    // 找到第一个未生成的章节
    const firstIncompleteChapter = sortedChapters.find(
      ch => !ch.content || ch.content.trim() === ''
    );
    
    if (!firstIncompleteChapter) {
      message.info('所有章节都已生成内容');
      return;
    }
    
    // 检查该章节是否可以生成
    if (!canGenerateChapter(firstIncompleteChapter)) {
      const reason = getGenerateDisabledReason(firstIncompleteChapter);
      message.warning(reason);
      return;
    }
    
    setBatchGenerateVisible(true);
  };

  // 渲染分析状态标签
  const renderAnalysisStatus = (chapterId: string) => {
    const task = analysisTasksMap[chapterId];
    
    if (!task) {
      return null;
    }
    
    switch (task.status) {
      case 'pending':
        return (
          <Tag icon={<SyncOutlined spin />} color="processing">
            等待分析
          </Tag>
        );
      case 'running':
        return (
          <Tag icon={<SyncOutlined spin />} color="processing">
            分析中 {task.progress}%
          </Tag>
        );
      case 'completed':
        return (
          <Tag icon={<CheckCircleOutlined />} color="success">
            已分析
          </Tag>
        );
      case 'failed':
        return (
          <Tooltip title={task.error_message}>
            <Tag icon={<CloseCircleOutlined />} color="error">
              分析失败
            </Tag>
          </Tooltip>
        );
      default:
        return null;
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{
        position: 'sticky',
        top: 0,
        zIndex: 10,
        backgroundColor: '#fff',
        padding: isMobile ? '12px 0' : '16px 0',
        marginBottom: isMobile ? 12 : 16,
        borderBottom: '1px solid #f0f0f0',
        display: 'flex',
        flexDirection: isMobile ? 'column' : 'row',
        gap: isMobile ? 12 : 0,
        justifyContent: 'space-between',
        alignItems: isMobile ? 'stretch' : 'center'
      }}>
        <h2 style={{ margin: 0, fontSize: isMobile ? 18 : 24 }}>章节管理</h2>
        <Space direction={isMobile ? 'vertical' : 'horizontal'} style={{ width: isMobile ? '100%' : 'auto' }}>
          <Button
            type="primary"
            icon={<RocketOutlined />}
            onClick={handleOpenBatchGenerate}
            disabled={chapters.length === 0}
            block={isMobile}
            size={isMobile ? 'middle' : 'middle'}
            style={{ background: '#722ed1', borderColor: '#722ed1' }}
          >
            批量生成
          </Button>
          <Button
            type="default"
            icon={<DownloadOutlined />}
            onClick={handleExport}
            disabled={chapters.length === 0}
            block={isMobile}
            size={isMobile ? 'middle' : 'middle'}
          >
            导出为TXT
          </Button>
          {!isMobile && <Tag color="blue">章节由大纲管理，请在大纲页面添加/删除</Tag>}
        </Space>
      </div>

      <div style={{ flex: 1, overflowY: 'auto' }}>
        {chapters.length === 0 ? (
        <Empty description="还没有章节，开始创作吧！" />
      ) : (
        <Card style={cardStyles.base}>
          <List
            dataSource={sortedChapters}
            renderItem={(item) => (
              <List.Item
                style={{
                  padding: '16px 0',
                  borderRadius: 8,
                  transition: 'background 0.3s ease',
                  flexDirection: isMobile ? 'column' : 'row',
                  alignItems: isMobile ? 'flex-start' : 'center'
                }}
                actions={isMobile ? undefined : [
                  <Button
                    icon={<EditOutlined />}
                    onClick={() => handleOpenEditor(item.id)}
                  >
                    编辑内容
                  </Button>,
                  (() => {
                    const task = analysisTasksMap[item.id];
                    const isAnalyzing = task && (task.status === 'pending' || task.status === 'running');
                    const hasContent = item.content && item.content.trim() !== '';
                    
                    return (
                      <Tooltip
                        title={
                          !hasContent ? '请先生成章节内容' :
                          isAnalyzing ? '分析进行中，请稍候...' :
                          ''
                        }
                      >
                        <Button
                          icon={isAnalyzing ? <SyncOutlined spin /> : <FundOutlined />}
                          onClick={() => handleShowAnalysis(item.id)}
                          disabled={!hasContent || isAnalyzing}
                          loading={isAnalyzing}
                        >
                          {isAnalyzing ? '分析中' : '查看分析'}
                        </Button>
                      </Tooltip>
                    );
                  })(),
                  <Button
                    type="text"
                    icon={<SettingOutlined />}
                    onClick={() => handleOpenModal(item.id)}
                  >
                    修改信息
                  </Button>,
                ]}
              >
                <div style={{ width: '100%' }}>
                  <List.Item.Meta
                    avatar={!isMobile && <FileTextOutlined style={{ fontSize: 32, color: '#1890ff' }} />}
                    title={
                      <div style={{ display: 'flex', alignItems: 'center', gap: isMobile ? 4 : 8, flexWrap: 'wrap', fontSize: isMobile ? 14 : 16 }}>
                        <span>第{item.chapter_number}章：{item.title}</span>
                        <Tag color={getStatusColor(item.status)}>{getStatusText(item.status)}</Tag>
                        <Badge count={`${item.word_count || 0}字`} style={{ backgroundColor: '#52c41a' }} />
                        {renderAnalysisStatus(item.id)}
                        {!canGenerateChapter(item) && (
                          <Tooltip title={getGenerateDisabledReason(item)}>
                            <Tag icon={<LockOutlined />} color="warning">
                              需前置章节
                            </Tag>
                          </Tooltip>
                        )}
                      </div>
                    }
                    description={
                      item.content ? (
                        <div style={{ marginTop: 8, color: 'rgba(0,0,0,0.65)', lineHeight: 1.6, fontSize: isMobile ? 12 : 14 }}>
                          {item.content.substring(0, isMobile ? 80 : 150)}
                          {item.content.length > (isMobile ? 80 : 150) && '...'}
                        </div>
                      ) : (
                        <span style={{ color: 'rgba(0,0,0,0.45)', fontSize: isMobile ? 12 : 14 }}>暂无内容</span>
                      )
                    }
                  />
                  
                  {isMobile && (
                    <Space style={{ marginTop: 12, width: '100%', justifyContent: 'flex-end' }} wrap>
                      <Button
                        type="text"
                        icon={<EditOutlined />}
                        onClick={() => handleOpenEditor(item.id)}
                        size="small"
                        title="编辑内容"
                      />
                      {(() => {
                        const task = analysisTasksMap[item.id];
                        const isAnalyzing = task && (task.status === 'pending' || task.status === 'running');
                        const hasContent = item.content && item.content.trim() !== '';
                        
                        return (
                          <Tooltip
                            title={
                              !hasContent ? '请先生成章节内容' :
                              isAnalyzing ? '分析中' :
                              '查看分析'
                            }
                          >
                            <Button
                              type="text"
                              icon={isAnalyzing ? <SyncOutlined spin /> : <FundOutlined />}
                              onClick={() => handleShowAnalysis(item.id)}
                              size="small"
                              disabled={!hasContent || isAnalyzing}
                              loading={isAnalyzing}
                            />
                          </Tooltip>
                        );
                      })()}
                      <Button
                        type="text"
                        icon={<SettingOutlined />}
                        onClick={() => handleOpenModal(item.id)}
                        size="small"
                        title="修改信息"
                      />
                    </Space>
                  )}
                </div>
              </List.Item>
            )}
          />
        </Card>
        )}
      </div>

      <Modal
        title={editingId ? '编辑章节信息' : '添加章节'}
        open={isModalOpen}
        onCancel={() => setIsModalOpen(false)}
        footer={null}
        centered={!isMobile}
        width={isMobile ? 'calc(100% - 32px)' : 520}
        style={isMobile ? {
          top: 20,
          paddingBottom: 0,
          maxWidth: 'calc(100vw - 32px)',
          margin: '0 16px'
        } : undefined}
        styles={{
          body: {
            maxHeight: isMobile ? 'calc(100vh - 150px)' : 'calc(80vh - 110px)',
            overflowY: 'auto'
          }
        }}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item
            label="章节标题"
            name="title"
            tooltip="章节标题由大纲管理，建议在大纲页面统一修改"
          >
            <Input placeholder="输入章节标题" disabled />
          </Form.Item>

          <Form.Item
            label="章节序号"
            name="chapter_number"
            tooltip="章节序号由大纲的顺序决定，无法修改。请在大纲页面使用上移/下移功能调整顺序"
          >
            <Input type="number" placeholder="章节排序序号" disabled />
          </Form.Item>

          <Form.Item label="状态" name="status">
            <Select placeholder="选择状态">
              <Select.Option value="draft">草稿</Select.Option>
              <Select.Option value="writing">创作中</Select.Option>
              <Select.Option value="completed">已完成</Select.Option>
            </Select>
          </Form.Item>

          <Form.Item>
            <Space style={{ float: 'right' }}>
              <Button onClick={() => setIsModalOpen(false)}>取消</Button>
              <Button type="primary" htmlType="submit">
                更新
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑章节内容"
        open={isEditorOpen}
        onCancel={() => {
          if (isGenerating) {
            message.warning('AI正在创作中，请等待完成后再关闭');
            return;
          }
          setIsEditorOpen(false);
        }}
        closable={!isGenerating}
        maskClosable={!isGenerating}
        keyboard={!isGenerating}
        width={isMobile ? 'calc(100% - 32px)' : '85%'}
        centered={!isMobile}
        style={isMobile ? {
          top: 20,
          paddingBottom: 0,
          maxWidth: 'calc(100vw - 32px)',
          margin: '0 16px'
        } : undefined}
        styles={{
          body: {
            maxHeight: isMobile ? 'calc(100vh - 150px)' : 'calc(100vh - 110px)',
            overflowY: 'auto',
            padding: isMobile ? '16px 12px' : '8px'
          }
        }}
        footer={null}
      >
        <Form form={editorForm} layout="vertical" onFinish={handleEditorSubmit}>
          <Form.Item
            label="章节标题"
            tooltip="章节标题由大纲统一管理，建议在大纲页面修改以保持一致性"
          >
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item
                name="title"
                noStyle
              >
                <Input size="large" disabled style={{ flex: 1 }} />
              </Form.Item>
              {editingId && (() => {
                const currentChapter = chapters.find(c => c.id === editingId);
                const canGenerate = currentChapter ? canGenerateChapter(currentChapter) : false;
                const disabledReason = currentChapter ? getGenerateDisabledReason(currentChapter) : '';
                
                return (
                  <Tooltip title={!canGenerate ? disabledReason : '根据大纲和前置章节内容创作'}>
                    <Button
                      type="primary"
                      icon={canGenerate ? <ThunderboltOutlined /> : <LockOutlined />}
                      onClick={() => currentChapter && showGenerateModal(currentChapter)}
                      loading={isContinuing}
                      disabled={!canGenerate}
                      danger={!canGenerate}
                      size="large"
                      style={{ fontWeight: 'bold' }}
                    >
                      {isMobile ? 'AI创作' : 'AI创作章节内容'}
                    </Button>
                  </Tooltip>
                );
              })()}
            </Space.Compact>
          </Form.Item>

          <Form.Item
            label="写作风格"
            tooltip="选择AI创作时使用的写作风格，可在写作风格菜单中管理"
            required
          >
            <Select
              placeholder="请选择写作风格"
              value={selectedStyleId}
              onChange={setSelectedStyleId}
              size="large"
              disabled={isGenerating}
              style={{ width: '100%' }}
              status={!selectedStyleId ? 'error' : undefined}
            >
              {writingStyles.map(style => (
                <Select.Option key={style.id} value={style.id}>
                  {style.name}
                  {style.is_default && ' (默认)'}
                  {style.description && ` - ${style.description}`}
                </Select.Option>
              ))}
            </Select>
            {!selectedStyleId && (
              <div style={{ color: '#ff4d4f', fontSize: 12, marginTop: 4 }}>
                请选择写作风格
              </div>
            )}
          </Form.Item>

          <Form.Item
            label="目标字数"
            tooltip="AI生成章节时的目标字数，实际生成字数可能略有偏差"
          >
            <InputNumber
              min={500}
              max={10000}
              step={100}
              value={targetWordCount}
              onChange={(value) => setTargetWordCount(value || 3000)}
              size="large"
              disabled={isGenerating}
              style={{ width: '100%' }}
              formatter={(value) => `${value} 字`}
              parser={(value) => value?.replace(' 字', '') as any}
            />
            <div style={{ color: '#666', fontSize: 12, marginTop: 4 }}>
              建议范围：500-10000字，默认3000字
            </div>
          </Form.Item>

          <Form.Item label="本次使用的模型">
            <LLMConfigSelector value={llmConfigId} onChange={setLLMConfigId} disabled={isGenerating} />
          </Form.Item>

          <Form.Item label="章节内容" name="content">
            <TextArea
              ref={contentTextAreaRef}
              rows={isMobile ? 12 : 20}
              placeholder="开始写作..."
              style={{ fontFamily: 'monospace', fontSize: isMobile ? 12 : 14 }}
              disabled={isGenerating}
            />
          </Form.Item>

          <Form.Item>
            <Space style={{ width: '100%', justifyContent: 'flex-end', flexDirection: isMobile ? 'column' : 'row', alignItems: isMobile ? 'stretch' : 'center' }}>
              <Space style={{ width: isMobile ? '100%' : 'auto' }}>
                <Button
                  onClick={() => {
                    if (isGenerating) {
                      message.warning('AI正在创作中，请等待完成后再关闭');
                      return;
                    }
                    setIsEditorOpen(false);
                  }}
                  block={isMobile}
                  disabled={isGenerating}
                >
                  取消
                </Button>
                <Button
                  type="primary"
                  htmlType="submit"
                  block={isMobile}
                  disabled={isGenerating}
                >
                  保存章节
                </Button>
              </Space>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {analysisChapterId && (
        <ChapterAnalysis
          chapterId={analysisChapterId}
          visible={analysisVisible}
          onClose={() => {
            setAnalysisVisible(false);
            
            // 延迟500ms后刷新该章节的分析状态，给后端足够时间完成数据库写入
            if (analysisChapterId) {
              const chapterIdToRefresh = analysisChapterId;
              
              setTimeout(() => {
                fetch(`/api/chapters/${chapterIdToRefresh}/analysis/status`)
                  .then(response => {
                    if (response.ok) {
                      return response.json();
                    }
                    throw new Error('获取状态失败');
                  })
                  .then((task: AnalysisTask) => {
                    setAnalysisTasksMap(prev => ({
                      ...prev,
                      [chapterIdToRefresh]: task
                    }));
                    
                    // 如果任务正在运行，启动轮询
                    if (task.status === 'pending' || task.status === 'running') {
                      startPollingTask(chapterIdToRefresh);
                    }
                  })
                  .catch(error => {
                    console.error('刷新分析状态失败:', error);
                    // 如果查询失败，再延迟尝试一次
                    setTimeout(() => {
                      fetch(`/api/chapters/${chapterIdToRefresh}/analysis/status`)
                        .then(response => response.ok ? response.json() : null)
                        .then((task: AnalysisTask | null) => {
                          if (task) {
                            setAnalysisTasksMap(prev => ({
                              ...prev,
                              [chapterIdToRefresh]: task
                            }));
                            if (task.status === 'pending' || task.status === 'running') {
                              startPollingTask(chapterIdToRefresh);
                            }
                          }
                        })
                        .catch(err => console.error('第二次刷新失败:', err));
                    }, 1000);
                  });
              }, 500);
            }
            
            setAnalysisChapterId(null);
          }}
        />
      )}

      {/* 批量生成对话框 */}
      <Modal
        title={
          <Space>
            <RocketOutlined style={{ color: '#722ed1' }} />
            <span>批量生成章节内容</span>
          </Space>
        }
        open={batchGenerateVisible}
        onCancel={() => {
          if (batchGenerating) {
            Modal.confirm({
              title: '确认取消',
              content: '批量生成正在进行中，确定要取消吗？',
              okText: '确定取消',
              cancelText: '继续生成',
              onOk: () => {
                handleCancelBatchGenerate();
                setBatchGenerateVisible(false);
              },
            });
          } else {
            setBatchGenerateVisible(false);
          }
        }}
        footer={null}
        width={600}
        centered
        closable={!batchGenerating}
        maskClosable={!batchGenerating}
      >
        {!batchGenerating ? (
          <Form
            layout="vertical"
            onFinish={handleBatchGenerate}
            initialValues={{
              startChapterNumber: sortedChapters.find(ch => !ch.content || ch.content.trim() === '')?.chapter_number || 1,
              count: 5,
              enableAnalysis: false,
              styleId: selectedStyleId,
              targetWordCount: 3000,
              llmConfigId: undefined,
            }}
          >
            <Alert
              message="批量生成说明"
              description={
                <ul style={{ margin: '8px 0 0 0', paddingLeft: 20 }}>
                  <li>严格按章节序号顺序生成，不可跳过</li>
                  <li>所有章节使用相同的写作风格和目标字数</li>
                  <li>任一章节失败则终止后续生成</li>
                </ul>
              }
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />

            <Form.Item
              label="起始章节"
              name="startChapterNumber"
              rules={[{ required: true, message: '请选择起始章节' }]}
            >
              <Select placeholder="选择起始章节" size="large">
                {sortedChapters
                  .filter(ch => !ch.content || ch.content.trim() === '')
                  .filter(ch => canGenerateChapter(ch))
                  .map(ch => (
                    <Select.Option key={ch.id} value={ch.chapter_number}>
                      第{ch.chapter_number}章：{ch.title}
                    </Select.Option>
                  ))}
              </Select>
            </Form.Item>

            <Form.Item
              label="生成数量"
              name="count"
              rules={[{ required: true, message: '请选择生成数量' }]}
            >
              <Radio.Group buttonStyle="solid" size="large">
                <Radio.Button value={5}>5章</Radio.Button>
                <Radio.Button value={10}>10章</Radio.Button>
                <Radio.Button value={15}>15章</Radio.Button>
                <Radio.Button value={20}>20章</Radio.Button>
              </Radio.Group>
            </Form.Item>

            <Form.Item
              label="写作风格"
              name="styleId"
              rules={[{ required: true, message: '请选择写作风格' }]}
              tooltip="批量生成时所有章节使用相同的写作风格"
            >
              <Select
                placeholder="请选择写作风格"
                size="large"
                showSearch
                optionFilterProp="children"
              >
                {writingStyles.map(style => (
                  <Select.Option key={style.id} value={style.id}>
                    {style.name}
                    {style.is_default && ' (默认)'}
                    {style.description && ` - ${style.description}`}
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>

            <Form.Item
              label="目标字数"
              tooltip="AI生成章节时的目标字数，实际生成字数可能略有偏差"
            >
              <Form.Item
                name="targetWordCount"
                rules={[{ required: true, message: '请设置目标字数' }]}
                noStyle
              >
                <InputNumber
                  min={500}
                  max={10000}
                  step={100}
                  size="large"
                  style={{ width: '100%' }}
                  formatter={(value) => `${value} 字`}
                  parser={(value) => value?.replace(' 字', '') as any}
                />
              </Form.Item>
              <div style={{ color: '#666', fontSize: 12, marginTop: 4 }}>
                建议范围：500-10000字，默认3000字
              </div>
            </Form.Item>

            <Form.Item
              label="同步分析"
              name="enableAnalysis"
              tooltip="开启后每章生成完立即分析，会增加约50%耗时，但能提升后续章节质量"
            >
              <Radio.Group>
                <Radio value={false}>
                  <Space direction="vertical" size={0}>
                    <span>不分析（推荐）</span>
                    <span style={{ fontSize: 12, color: '#666' }}>生成更快，后续可手动分析</span>
                  </Space>
                </Radio>
                <Radio value={true}>
                  <Space direction="vertical" size={0}>
                    <span>同步分析</span>
                    <span style={{ fontSize: 12, color: '#ff9800' }}>增加约50%耗时，提升质量</span>
                  </Space>
                </Radio>
              </Radio.Group>
            </Form.Item>

            <Form.Item label="本次使用的模型" name="llmConfigId">
              <LLMConfigSelector />
            </Form.Item>

            <Form.Item>
              <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
                <Button onClick={() => setBatchGenerateVisible(false)}>
                  取消
                </Button>
                <Button type="primary" htmlType="submit" icon={<RocketOutlined />}>
                  开始批量生成
                </Button>
              </Space>
            </Form.Item>
          </Form>
        ) : (
          <div>
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span>生成进度：</span>
                <span>
                  <strong style={{ color: '#1890ff', fontSize: 18 }}>
                    {batchProgress?.completed || 0} / {batchProgress?.total || 0}
                  </strong>
                  章
                </span>
              </div>
              <Progress
                percent={batchProgress ? Math.round((batchProgress.completed / batchProgress.total) * 100) : 0}
                status={batchProgress?.status === 'failed' ? 'exception' : 'active'}
                strokeColor={{
                  '0%': '#722ed1',
                  '100%': '#1890ff',
                }}
              />
            </div>

            {batchProgress?.current_chapter_number && (
              <Alert
                message={`正在生成第 ${batchProgress.current_chapter_number} 章...`}
                type="info"
                showIcon
                icon={<SyncOutlined spin />}
                style={{ marginBottom: 16 }}
              />
            )}

            {batchProgress?.estimated_time_minutes && batchProgress.completed === 0 && (
              <div style={{ marginBottom: 16, color: '#666', fontSize: 13 }}>
                ⏱️ 预计耗时：约 {batchProgress.estimated_time_minutes} 分钟
              </div>
            )}

            <Alert
              message="温馨提示"
              description={
                <ul style={{ margin: '8px 0 0 0', paddingLeft: 20 }}>
                  <li>批量生成需要一定时间，可以切换到其他页面</li>
                  <li>关闭页面后重新打开，会自动恢复任务进度</li>
                  <li>可以随时点击"取消任务"按钮中止生成</li>
                </ul>
              }
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
            />

            <div style={{ textAlign: 'center' }}>
              <Button
                danger
                icon={<StopOutlined />}
                onClick={() => {
                  Modal.confirm({
                    title: '确认取消',
                    content: '确定要取消批量生成吗？已生成的章节将保留。',
                    okText: '确定取消',
                    cancelText: '继续生成',
                    okButtonProps: { danger: true },
                    onOk: handleCancelBatchGenerate,
                  });
                }}
              >
                取消任务
              </Button>
            </div>
          </div>
        )}
      </Modal>

      {/* 单章节生成进度显示 */}
      <SSELoadingOverlay
        loading={isGenerating}
        progress={singleChapterProgress}
        message={singleChapterProgressMessage}
      />
    </div>
  );
}
