import { Navigate, Route, Routes } from 'react-router-dom'
import LoginScreen from './components/auth/LoginScreen.jsx'
import GraphTab from './components/graph/GraphTab.jsx'
import HistoryTab from './components/history/HistoryTab.jsx'
import Shell from './components/layout/Shell.jsx'
import QueryTab from './components/query/QueryTab.jsx'
import { useAuth } from './hooks/useAuth.js'
import { useAppState } from './store/appState.js'

function AuthGate({ auth }) {
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

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/query" replace />} />
      <Route path="/query" element={<QueryTab />} />
      <Route path="/graph" element={<GraphTab />} />
      <Route path="/history" element={<HistoryTab />} />
      <Route path="*" element={<Navigate to="/query" replace />} />
    </Routes>
  )
}

export default function App() {
  const auth = useAuth()
  const { isAuthenticated } = useAppState()

  return (
    <Shell isAuthenticated={isAuthenticated}>
      <AuthGate auth={auth} />
    </Shell>
  )
}
