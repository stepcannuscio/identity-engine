import { useAppState } from '../../store/appState.js'

export default function Header() {
  const { backend, setBackend, providerStatuses } = useAppState()
  const nextBackend = backend === 'local' ? 'external' : 'local'
  const externalReady = providerStatuses.some(
    (provider) => !provider.is_local && provider.available,
  )
  const tooltip =
    backend === 'local' && !externalReady
      ? 'Configure an external provider in Settings before switching'
      : backend === 'local'
        ? 'Queries processed on your device'
        : 'Queries sent to your configured external provider'

  const handleToggle = () => {
    if (backend === 'local' && !externalReady) {
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
        disabled={backend === 'local' && !externalReady}
      >
        <span className="backend-dot" aria-hidden="true" />
        <span className="backend-label">{backend}</span>
      </button>
    </header>
  )
}
