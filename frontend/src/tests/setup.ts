// vitest 5（SEC-15 根治批）：断言类型双参数化后不再读取全局 jest.Matchers，
// jest-dom 需走 /vitest 入口同时注册运行时 matchers 与 vitest 模块扩展类型。
import '@testing-library/jest-dom/vitest'
