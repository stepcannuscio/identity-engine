import { useAppState } from '../../store/appState.js'

export default function Header() {
  const { backend, setBackend, providerStatuses } = useAppState()
  const externalReady = providerStatuses.some(
    (provider) => !provider.is_local && provider.available,
  )
  const privateServerReady = providerStatuses.some(
    (provider) => provider.provider === 'private_server' && provider.available,
  )

  const nextBackend = backend === 'local'
    ? (privateServerReady ? 'private_server' : externalReady ? 'external' : 'local')
    : backend === 'private_server'
      ? (externalReady ? 'external' : 'local')
      : 'local'

  const backendLabel = backend === 'private_server' ? 'private server' : backend

  const tooltip =
    backend === 'local' && !externalReady && !privateServerReady
      ? 'Configure a private server or external provider in Settings before switching'
      : backend === 'local'
        ? 'Queries processed on your device'
        : backend === 'private_server'
          ? 'Queries sent to your private server'
          : 'Queries sent to your configured external provider'

  const handleToggle = () => {
    if (backend === 'local' && !externalReady && !privateServerReady) {
      return
    }
    setBackend(nextBackend)
  }

  return (
    <header className="app-header">
      <div className="header-brand">identity engine</div>
      <button
        type="button"
        className={`backend-pill ${backend}`}
        onClick={handleToggle}
        title={tooltip}
        disabled={backend === 'local' && !externalReady && !privateServerReady}
      >
        <span className="backend-dot" aria-hidden="true" />
        <span className="backend-label">{backendLabel}</span>
      </button>
    </header>
  )
}
