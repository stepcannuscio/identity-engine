import {
  createElement,
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from 'react'

const AppStateContext = createContext(null)

function nextToastId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID()
  }
  return `toast-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function AppStateProvider({ children }) {
  const [token, setToken] = useState(() => sessionStorage.getItem('session_token'))
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [expiresAt, setExpiresAt] = useState(null)
  const [backend, setBackend] = useState('local')
  const [messages, setMessages] = useState([])
  const [isStreaming, setStreaming] = useState(false)
  const [toasts, setToasts] = useState([])

  const setAuthState = useCallback((nextToken, nextExpiresAt) => {
    setToken(nextToken)
    setExpiresAt(nextExpiresAt)
    setIsAuthenticated(Boolean(nextToken))
  }, [])

  const clearAuthState = useCallback(() => {
    sessionStorage.removeItem('session_token')
    setToken(null)
    setExpiresAt(null)
    setIsAuthenticated(false)
  }, [])

  const addMessage = useCallback((message) => {
    setMessages((current) => [...current, message])
  }, [])

  const clearMessages = useCallback(() => {
    setMessages([])
  }, [])

  const removeToast = useCallback((toastId) => {
    setToasts((current) => current.filter((toast) => toast.id !== toastId))
  }, [])

  const addToast = useCallback(
    ({ message, tone = 'warning', duration = 5000 }) => {
      const id = nextToastId()
      setToasts((current) => [...current, { id, message, tone }])

      if (duration > 0) {
        window.setTimeout(() => {
          removeToast(id)
        }, duration)
      }

      return id
    },
    [removeToast],
  )

  const value = useMemo(
    () => ({
      token,
      isAuthenticated,
      expiresAt,
      setAuthState,
      clearAuthState,
      backend,
      setBackend,
      messages,
      addMessage,
      clearMessages,
      isStreaming,
      setStreaming,
      toasts,
      addToast,
      removeToast,
    }),
    [
      addMessage,
      addToast,
      backend,
      clearAuthState,
      clearMessages,
      expiresAt,
      isAuthenticated,
      isStreaming,
      messages,
      removeToast,
      setAuthState,
      toasts,
      token,
    ],
  )

  return createElement(AppStateContext.Provider, { value }, children)
}

export function useAppState() {
  const context = useContext(AppStateContext)
  if (!context) {
    throw new Error('useAppState must be used within AppStateProvider')
  }
  return context
}
