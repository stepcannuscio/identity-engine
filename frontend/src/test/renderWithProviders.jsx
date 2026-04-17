import { useEffect, useRef } from 'react'
import { render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { AppStateProvider, useAppState } from '../store/appState.js'

function AppStateInitializer({ appState }) {
  const didInitialize = useRef(false)
  const {
    setAuthState,
    clearAuthState,
    setBackend,
    clearMessages,
    addMessage,
    setStreaming,
    addToast,
  } = useAppState()

  useEffect(() => {
    if (didInitialize.current) {
      return
    }
    didInitialize.current = true

    if (!appState || Object.keys(appState).length === 0) {
      return
    }

    if ('token' in appState && appState.token) {
      sessionStorage.setItem('session_token', appState.token)
      setAuthState(appState.token, appState.expiresAt ?? null)
    } else if ('token' in appState) {
      sessionStorage.removeItem('session_token')
      clearAuthState()
    }

    if ('backend' in appState) {
      setBackend(appState.backend ?? 'local')
    }

    if ('messages' in appState) {
      clearMessages()
      for (const message of appState.messages ?? []) {
        addMessage(message)
      }
    }

    if ('isStreaming' in appState) {
      setStreaming(Boolean(appState.isStreaming))
    }

    if ('toasts' in appState) {
      for (const toast of appState.toasts ?? []) {
        addToast({
          message: toast.message,
          tone: toast.tone,
          duration: 0,
        })
      }
    }
  }, [
    addMessage,
    addToast,
    appState,
    clearAuthState,
    clearMessages,
    setAuthState,
    setBackend,
    setStreaming,
  ])

  return null
}

export function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  })
}

export function createWrapper(options = {}) {
  const {
    route = '/',
    queryClient = createTestQueryClient(),
    appState = {},
  } = options

  return function TestWrapper({ children }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          <AppStateProvider>
            <AppStateInitializer appState={appState} />
            {children}
          </AppStateProvider>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
}

export function renderWithProviders(ui, options = {}) {
  const wrapper = createWrapper(options)
  return {
    queryClient: options.queryClient,
    ...render(ui, { wrapper }),
  }
}
