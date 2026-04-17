import { screen } from '@testing-library/react'
import { vi } from 'vitest'
import App from '../App.jsx'
import { getTeachBootstrap } from '../api/endpoints.js'
import { renderWithProviders } from './renderWithProviders.jsx'
import { useAuth } from '../hooks/useAuth.js'

vi.mock('../hooks/useAuth.js', () => ({
  useAuth: vi.fn(),
}))

vi.mock('../api/endpoints.js', () => ({
  getTeachBootstrap: vi.fn(),
}))

vi.mock('../components/auth/LoginScreen.jsx', () => ({
  default: () => <div>Login screen stub</div>,
}))

vi.mock('../components/query/QueryTab.jsx', () => ({
  default: () => <div>Query tab stub</div>,
}))

vi.mock('../components/teach/TeachTab.jsx', () => ({
  default: () => <div>Teach tab stub</div>,
}))

vi.mock('../components/graph/GraphTab.jsx', () => ({
  default: () => <div>Graph tab stub</div>,
}))

vi.mock('../components/history/HistoryTab.jsx', () => ({
  default: () => <div>History tab stub</div>,
}))

function createAuth(overrides = {}) {
  return {
    token: null,
    expiresAt: null,
    isAuthenticated: false,
    isChecking: false,
    login: vi.fn(),
    logout: vi.fn(),
    validateToken: vi.fn(),
    ...overrides,
  }
}

describe('App', () => {
  beforeEach(() => {
    getTeachBootstrap.mockResolvedValue({
      onboarding_completed: true,
      preferred_backend: 'local',
      active_profile: 'private_local_first',
      providers: [],
      profiles: [],
      security_posture: { platform: 'macos', supported: true, checks: [] },
      cards: [],
      questions: [],
    })
  })

  it('shows the session check state before auth is resolved', () => {
    useAuth.mockReturnValue(createAuth({ isChecking: true }))

    renderWithProviders(<App />)

    expect(screen.getByText('Checking session...')).toBeInTheDocument()
    expect(screen.queryByText('Login screen stub')).not.toBeInTheDocument()
  })

  it('renders the login screen when the user is not authenticated', () => {
    useAuth.mockReturnValue(createAuth())

    renderWithProviders(<App />)

    expect(screen.getByText('Login screen stub')).toBeInTheDocument()
  })

  it('redirects authenticated users to the query route by default when onboarding is complete', async () => {
    useAuth.mockReturnValue(createAuth({ isAuthenticated: true }))

    renderWithProviders(<App />, {
      route: '/',
      appState: { token: 'session-token' },
    })

    expect(await screen.findByText('Query tab stub')).toBeInTheDocument()
  })

  it('redirects authenticated users to Teach when onboarding is incomplete', async () => {
    getTeachBootstrap.mockResolvedValue({
      onboarding_completed: false,
      preferred_backend: 'local',
      active_profile: null,
      providers: [],
      profiles: [],
      security_posture: { platform: 'macos', supported: true, checks: [] },
      cards: [],
      questions: [],
    })
    useAuth.mockReturnValue(createAuth({ isAuthenticated: true }))

    renderWithProviders(<App />, {
      route: '/',
      appState: { token: 'session-token' },
    })

    expect(await screen.findByText('Teach tab stub')).toBeInTheDocument()
  })

  it('renders the requested authenticated route', async () => {
    useAuth.mockReturnValue(createAuth({ isAuthenticated: true }))

    renderWithProviders(<App />, {
      route: '/graph',
      appState: { token: 'session-token' },
    })

    expect(await screen.findByText('Graph tab stub')).toBeInTheDocument()
  })
})
