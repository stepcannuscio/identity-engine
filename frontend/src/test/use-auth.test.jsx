import { act, renderHook, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { useAuth } from '../hooks/useAuth.js'
import { useAppState } from '../store/appState.js'
import { createWrapper } from './renderWithProviders.jsx'
import {
  getAuthStatus,
  login as loginRequest,
  logout as logoutRequest,
} from '../api/endpoints.js'

vi.mock('../api/endpoints.js', () => ({
  getAuthStatus: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
}))

function renderUseAuth(options) {
  return renderHook(
    () => ({
      auth: useAuth(),
      state: useAppState(),
    }),
    { wrapper: createWrapper(options) },
  )
}

describe('useAuth', () => {
  it('boots from session storage and validates the token', async () => {
    sessionStorage.setItem('session_token', 'session-token')
    getAuthStatus.mockResolvedValue({ expires_at: '2026-04-18T12:00:00Z' })

    const { result } = renderUseAuth()

    await waitFor(() => {
      expect(result.current.auth.isAuthenticated).toBe(true)
    })

    expect(getAuthStatus).toHaveBeenCalledTimes(1)
    expect(result.current.state.token).toBe('session-token')
    expect(result.current.state.expiresAt).toBe('2026-04-18T12:00:00Z')
  })

  it('clears the session when the backend reports an expired token', async () => {
    sessionStorage.setItem('session_token', 'expired-token')
    getAuthStatus.mockResolvedValue({ expires_at: '2026-04-16T12:00:00Z' })

    const { result } = renderUseAuth()

    await waitFor(() => {
      expect(result.current.auth.isAuthenticated).toBe(false)
    })

    expect(sessionStorage.getItem('session_token')).toBeNull()
    expect(result.current.state.messages).toEqual([])
  })

  it('polls auth status every five minutes while authenticated', async () => {
    vi.useFakeTimers()
    sessionStorage.setItem('session_token', 'session-token')
    getAuthStatus.mockResolvedValue({ expires_at: '2026-04-18T12:00:00Z' })

    renderUseAuth()

    await act(async () => {
      await Promise.resolve()
    })
    const initialCalls = getAuthStatus.mock.calls.length
    expect(initialCalls).toBeGreaterThanOrEqual(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000)
    })

    expect(getAuthStatus.mock.calls.length).toBeGreaterThan(initialCalls)
  })

  it('stores the session token on login', async () => {
    loginRequest.mockResolvedValue({
      token: 'new-token',
      expires_at: '2026-04-18T12:00:00Z',
    })

    const { result } = renderUseAuth()

    await act(async () => {
      await result.current.auth.login('open sesame')
    })

    expect(loginRequest).toHaveBeenCalledWith('open sesame')
    expect(sessionStorage.getItem('session_token')).toBe('new-token')
    expect(result.current.auth.isAuthenticated).toBe(true)
  })

  it('clears messages and auth state on logout even when the request fails', async () => {
    getAuthStatus.mockResolvedValue({ expires_at: '2026-04-18T12:00:00Z' })
    logoutRequest.mockRejectedValue(new Error('network failed'))

    const { result } = renderUseAuth({
      appState: {
        token: 'session-token',
        messages: [{ role: 'user', content: 'keep this private' }],
      },
    })

    await waitFor(() => {
      expect(result.current.state.messages).toHaveLength(1)
    })

    await act(async () => {
      try {
        await result.current.auth.logout()
      } catch (error) {
        expect(error.message).toBe('network failed')
      }
    })

    await waitFor(() => {
      expect(sessionStorage.getItem('session_token')).toBeNull()
      expect(result.current.auth.isAuthenticated).toBe(false)
      expect(result.current.state.messages).toEqual([])
    })
  })

  it('clears the session when status validation fails', async () => {
    sessionStorage.setItem('session_token', 'broken-token')
    getAuthStatus.mockRejectedValue(new Error('backend unavailable'))

    const { result } = renderUseAuth()

    await waitFor(() => {
      expect(result.current.auth.isAuthenticated).toBe(false)
    })

    expect(sessionStorage.getItem('session_token')).toBeNull()
  })
})
