import axios from 'axios'

export const API_BASE_URL =
  import.meta.env.VITE_API_URL || (import.meta.env.DEV ? '/api' : '')

const client = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
})

client.interceptors.request.use((config) => {
  const token = sessionStorage.getItem('session_token')
  if (token) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      sessionStorage.removeItem('session_token')
      window.location.reload()
    }
    return Promise.reject(error)
  },
)

export default client
