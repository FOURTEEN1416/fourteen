// Vite 资源后缀 ?raw 的 ambient 声明（本仓无 @types/node，静态源码守卫统一走 ?raw 导入）。
declare module '*?raw' {
  const src: string
  export default src
}
