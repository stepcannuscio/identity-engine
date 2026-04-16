import { useCallback, useEffect, useState } from 'react'
import {
  getAuthStatus,
  login as loginRequest,
  logout as logoutRequest,
} from '../api/endpoints.js'
import { useAppState } from '../store/appState.js'

const FIVE_MINUTES_MS = 5 * 60 * 1000

export function useAuth() {
  const {
    token,
    isAuthenticated,
    expiresAt,
    setAuthState,
    clearAuthState,
    clearMessages,
  } = useAppState()
  const [isChecking, setIsChecking] = useState(Boolean(token))

  const clearSession = useCallback(() => {
    clearAuthState()
    clearMessages()
  }, [clearAuthState, clearMessages])

  const validateToken = useCallback(
    async (candidateToken = sessionStorage.getItem('session_token')) => {
      if (!candidateToken) {
        clearSession()
        setIsChecking(false)
        return false
      }

      setIsChecking(true)
      try {
        const status = await getAuthStatus()
        if (status.expires_at && new Date(status.expires_at) <= new Date()) {
          clearSession()
          return false
        }

        setAuthState(candidateToken, status.expires_at)
        return true
      } catch {
        clearSession()
        return false
      } finally {
        setIsChecking(false)
      }
    },
    [clearSession, setAuthState],
  )

  useEffect(() => {
    const storedToken = sessionStorage.getItem('session_token')
    if (!storedToken) {
      clearSession()
      setIsChecking(false)
      return
    }

    if (storedToken !== token) {
      setAuthState(storedToken, null)
    }

    void validateToken(storedToken)
  }, [clearSession, setAuthState, token, validateToken])

  useEffect(() => {
    if (!token) {
      return undefined
    }

    const interval = window.setInterval(() => {
      void validateToken(token)
    }, FIVE_MINUTES_MS)

    return () => window.clearInterval(interval)
  }, [token, validateToken])

  const login = useCallback(
    async (passphrase) => {
      const response = await loginRequest(passphrase)
      sessionStorage.setItem('session_token', response.token)
      setAuthState(response.token, response.expires_at)
      return response
    },
    [setAuthState],
  )

  const logout = useCallback(async () => {
    try {
      if (sessionStorage.getItem('session_token')) {
        await logoutRequest()
      }
    } finally {
      clearSession()
    }
  }, [clearSession])

  return {
    token,
    expiresAt,
    isAuthenticated,
    isChecking,
    login,
    logout,
    validateToken,
  }
}
