import { Suspense, lazy, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Navigate, Route, Routes } from 'react-router-dom'
import { getTeachBootstrap } from './api/endpoints.js'
import LoginScreen from './components/auth/LoginScreen.jsx'
import Shell from './components/layout/Shell.jsx'
import { useAuth } from './hooks/useAuth.js'
import { useAppState } from './store/appState.js'

const TeachTab = lazy(() => import('./components/teach/TeachTab.jsx'))
const SettingsTab = lazy(() => import('./components/settings/SettingsTab.jsx'))
const QueryTab = lazy(() => import('./components/query/QueryTab.jsx'))
const GraphTab = lazy(() => import('./components/graph/GraphTab.jsx'))
const HistoryTab = lazy(() => import('./components/history/HistoryTab.jsx'))

function RouteFallback({ message = 'Loading your workspace...' }) {
  return (
    <div className="screen-state">
      <p>{message}</p>
    </div>
  )
}

function LazyRoute({ children, message }) {
  return (
    <Suspense fallback={<RouteFallback message={message} />}>
      {children}
    </Suspense>
  )
}

function AuthGate({ auth, bootstrapQuery }) {
  const { onboardingCompleted } = useAppState()
  const resolvedOnboardingCompleted =
    bootstrapQuery.data?.onboarding_completed ?? onboardingCompleted

  if (auth.isChecking) {
    return (
      <div className="screen-state">
        <p>Checking session...</p>
      </div>
    )
  }

  if (!auth.isAuthenticated) {
    return <LoginScreen auth={auth} />
  }

  if (bootstrapQuery.isLoading) {
    return (
      <div className="screen-state">
        <p>Loading your workspace...</p>
      </div>
    )
  }

  return (
    <Routes>
      <Route path="/" element={<Navigate to={resolvedOnboardingCompleted ? '/query' : '/teach'} replace />} />
      <Route
        path="/teach"
        element={(
          <LazyRoute>
            <TeachTab bootstrapQuery={bootstrapQuery} />
          </LazyRoute>
        )}
      />
      <Route
        path="/settings"
        element={(
          <LazyRoute>
            <SettingsTab bootstrapQuery={bootstrapQuery} />
          </LazyRoute>
        )}
      />
      <Route
        path="/query"
        element={(
          <LazyRoute>
            <QueryTab />
          </LazyRoute>
        )}
      />
      <Route
        path="/graph"
        element={(
          <LazyRoute>
            <GraphTab />
          </LazyRoute>
        )}
      />
      <Route
        path="/history"
        element={(
          <LazyRoute>
            <HistoryTab />
          </LazyRoute>
        )}
      />
      <Route path="*" element={<Navigate to={resolvedOnboardingCompleted ? '/query' : '/teach'} replace />} />
    </Routes>
  )
}

export default function App() {
  const auth = useAuth()
  const { isAuthenticated, setTeachState, setBackend } = useAppState()
  const bootstrapQuery = useQuery({
    queryKey: ['teachBootstrap'],
    queryFn: getTeachBootstrap,
    enabled: auth.isAuthenticated,
    retry: false,
  })

  useEffect(() => {
    if (!bootstrapQuery.data) {
      return
    }
    setTeachState(bootstrapQuery.data)
    if (bootstrapQuery.data.preferred_backend) {
      setBackend(bootstrapQuery.data.preferred_backend)
    }
  }, [bootstrapQuery.data, setBackend, setTeachState])

  return (
    <Shell isAuthenticated={isAuthenticated}>
      <AuthGate auth={auth} bootstrapQuery={bootstrapQuery} />
    </Shell>
  )
}
