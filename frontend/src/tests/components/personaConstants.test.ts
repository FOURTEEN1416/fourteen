/**
 * 前后端一致性锁定 —— constants/persona.ts 是唯一真源。
 *
 * 这里的期望值是后端真值的**手工镜像**（来源注释在各常量上），
 * 后端维度/情绪集变更时必须同步改 persona.ts + 本测试。
 * 2026-09-19 审计：RoleSettingsTabs 与 characterBuilderStore 曾各自写死
 * 后端不存在的 speaking_style 键，StatusCenter 情绪配色只命中 4/10。
 */
import { describe, it, expect } from 'vitest'
import {
  PERSONALITY_LABELS,
  SPEAKING_STYLE_LABELS,
  EMOTION_COLORS,
  DEFAULT_PERSONALITY,
  DEFAULT_SPEAKING_STYLE,
  AFFINITY_STAGES,
  normalizePersonality,
  normalizeSpeakingStyle,
} from '../../constants/persona'

describe('speaking_style 键 ↔ my_character/persona_card.py SpeakingStyle', () => {
  it('恰好覆盖后端四维，不含任何后端不存在的键', () => {
    expect(Object.keys(SPEAKING_STYLE_LABELS).sort()).toEqual(
      ['directness', 'expressiveness', 'formality', 'humor'].sort(),
    )
  })
  it('默认值键集与标签键集一致', () => {
    expect(Object.keys(DEFAULT_SPEAKING_STYLE).sort()).toEqual(Object.keys(SPEAKING_STYLE_LABELS).sort())
  })
})

describe('personality 键 ↔ 后端人格卡主维度', () => {
  it('标签与默认值键集一致', () => {
    expect(Object.keys(DEFAULT_PERSONALITY).sort()).toEqual(Object.keys(PERSONALITY_LABELS).sort())
  })
  it('不含历史上写死的 liveliness/gentleness 等伪键', () => {
    const all = { ...PERSONALITY_LABELS, ...SPEAKING_STYLE_LABELS }
    expect(all).not.toHaveProperty('liveliness')
    expect(all).not.toHaveProperty('gentleness')
  })
})

describe('情绪配色 ↔ my_character/emotion_engine.py Emotion 枚举中文值', () => {
  const BACKEND_EMOTIONS = ['开心', '伤心', '生气', '撒娇', '吃醋', '傲娇', '温柔', '调皮', '疲惫', '平常']
  it('恰好覆盖后端 10 种情绪（LLM 输出被 prompt 限定在此集合）', () => {
    expect(Object.keys(EMOTION_COLORS).sort()).toEqual([...BACKEND_EMOTIONS].sort())
  })
  it('每个情绪都有非空色值', () => {
    for (const e of BACKEND_EMOTIONS) expect(EMOTION_COLORS[e]).toMatch(/^bg-/)
  })
})

describe('normalize 函数：存量脏数据收敛到规范键集', () => {
  it('剔除后端不存在的旧键（liveliness/gentleness/catchphrases）', () => {
    const dirty = { formality: 0.3, humor: 0.8, liveliness: 0.5, gentleness: 0.7, catchphrases: [] as string[] }
    expect(normalizeSpeakingStyle(dirty)).toEqual({ formality: 0.3, expressiveness: 0.5, humor: 0.8, directness: 0.5 })
  })
  it('缺失键补默认值，null/undefined 输入返回整套默认', () => {
    expect(normalizeSpeakingStyle({ formality: 0.2 })).toEqual({ ...DEFAULT_SPEAKING_STYLE, formality: 0.2 })
    expect(normalizePersonality(null)).toEqual(DEFAULT_PERSONALITY)
    expect(normalizeSpeakingStyle(undefined)).toEqual(DEFAULT_SPEAKING_STYLE)
  })
  it('非数值脏值（字符串/数组）回落默认', () => {
    expect(normalizePersonality({ warmth: '0.9', jealousy: [1] })).toEqual(DEFAULT_PERSONALITY)
  })
})

describe('亲密阶段 ↔ shisi/emotion_stage/stage_config.py default()', () => {
  it('四段连续覆盖 0-100', () => {
    expect(AFFINITY_STAGES.map(s => s.name)).toEqual(['陌生', '熟悉', '亲密', '羁绊'])
    expect(AFFINITY_STAGES[0].min).toBe(0)
    expect(AFFINITY_STAGES[AFFINITY_STAGES.length - 1].max).toBe(100)
    for (let i = 1; i < AFFINITY_STAGES.length; i++) {
      expect(AFFINITY_STAGES[i].min).toBe(AFFINITY_STAGES[i - 1].max)
    }
  })
})
