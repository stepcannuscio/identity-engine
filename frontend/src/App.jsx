import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Navigate, Route, Routes } from 'react-router-dom'
import { getTeachBootstrap } from './api/endpoints.js'
import LoginScreen from './components/auth/LoginScreen.jsx'
import GraphTab from './components/graph/GraphTab.jsx'
import HistoryTab from './components/history/HistoryTab.jsx'
import Shell from './components/layout/Shell.jsx'
import QueryTab from './components/query/QueryTab.jsx'
import SettingsTab from './components/settings/SettingsTab.jsx'
import TeachTab from './components/teach/TeachTab.jsx'
import { useAuth } from './hooks/useAuth.js'
import { useAppState } from './store/appState.js'

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
      <Route path="/teach" element={<TeachTab bootstrapQuery={bootstrapQuery} />} />
      <Route path="/settings" element={<SettingsTab bootstrapQuery={bootstrapQuery} />} />
      <Route path="/query" element={<QueryTab />} />
      <Route path="/graph" element={<GraphTab />} />
      <Route path="/history" element={<HistoryTab />} />
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
