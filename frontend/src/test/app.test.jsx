import { screen } from '@testing-library/react'
import { vi } from 'vitest'
import App from '../App.jsx'
import { renderWithProviders } from './renderWithProviders.jsx'
import { useAuth } from '../hooks/useAuth.js'

vi.mock('../hooks/useAuth.js', () => ({
  useAuth: vi.fn(),
}))

vi.mock('../components/auth/LoginScreen.jsx', () => ({
  default: () => <div>Login screen stub</div>,
}))

vi.mock('../components/query/QueryTab.jsx', () => ({
  default: () => <div>Query tab stub</div>,
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

  it('redirects authenticated users to the query route by default', () => {
    useAuth.mockReturnValue(createAuth({ isAuthenticated: true }))

    renderWithProviders(<App />, {
      route: '/',
      appState: { token: 'session-token' },
    })

    expect(screen.getByText('Query tab stub')).toBeInTheDocument()
  })

  it('renders the requested authenticated route', () => {
    useAuth.mockReturnValue(createAuth({ isAuthenticated: true }))

    renderWithProviders(<App />, {
      route: '/graph',
      appState: { token: 'session-token' },
    })

    expect(screen.getByText('Graph tab stub')).toBeInTheDocument()
  })
})
