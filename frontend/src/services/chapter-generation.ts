import type { ChapterGenerateRequest } from '../types';

interface ChapterGenerateRequestOptions {
  styleId?: number;
  targetWordCount?: number;
  oneTimePrompt?: string;
  llmConfigId?: string;
}

export function normalizeOneTimePrompt(oneTimePrompt?: string): string | undefined {
  const normalizedPrompt = oneTimePrompt?.trim();
  return normalizedPrompt || undefined;
}

export function buildChapterGenerateRequest({
  styleId,
  targetWordCount,
  oneTimePrompt,
  llmConfigId,
}: ChapterGenerateRequestOptions): ChapterGenerateRequest {
  const normalizedPrompt = normalizeOneTimePrompt(oneTimePrompt);

  return {
    style_id: styleId,
    target_word_count: targetWordCount,
    ...(llmConfigId ? { llm_config_id: llmConfigId } : {}),
    ...(normalizedPrompt ? { one_time_prompt: normalizedPrompt } : {}),
  };
}
