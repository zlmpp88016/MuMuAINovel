import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildChapterGenerateRequest,
  normalizeOneTimePrompt,
} from '../src/services/chapter-generation.ts';

test('确认生成时传递裁剪后的一次性 Prompt', () => {
  const request = buildChapterGenerateRequest({
    styleId: 3,
    targetWordCount: 2500,
    oneTimePrompt: '  加强雨夜的悬疑氛围。\n',
    llmConfigId: 'config-chapter',
  });

  assert.deepEqual(request, {
    style_id: 3,
    target_word_count: 2500,
    one_time_prompt: '加强雨夜的悬疑氛围。',
    llm_config_id: 'config-chapter',
  });
});

test('空白一次性 Prompt 保持旧请求结构', () => {
  const request = buildChapterGenerateRequest({
    styleId: 3,
    targetWordCount: 2500,
    oneTimePrompt: '  \n\t ',
  });

  assert.deepEqual(request, {
    style_id: 3,
    target_word_count: 2500,
  });
});

test('一次性 Prompt 归一化兼容未填写值', () => {
  assert.equal(normalizeOneTimePrompt(), undefined);
  assert.equal(normalizeOneTimePrompt('   '), undefined);
  assert.equal(normalizeOneTimePrompt('  保持短句。  '), '保持短句。');
});
