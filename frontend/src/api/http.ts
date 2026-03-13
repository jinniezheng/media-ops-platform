import axios from 'axios'

// 开发模式: 前端 5174 → 后端 8000 (跨域)
// 生产模式: 同源部署，baseURL 为空即可
const isDev = import.meta.env.DEV

const http = axios.create({
  baseURL: isDev ? 'http://localhost:8000' : '',
  timeout: 120000,
})

// 请求拦截器：自动附加 Authorization header
http.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器：401 自动跳转登录页
http.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default http
