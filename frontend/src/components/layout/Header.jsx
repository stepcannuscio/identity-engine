import { useAppState } from '../../store/appState.js'

export default function Header() {
  const { backend, setBackend } = useAppState()
  const nextBackend = backend === 'local' ? 'external' : 'local'
  const tooltip =
    backend === 'local'
      ? 'Queries processed on your device'
      : 'Queries sent to Claude API (ZDR enabled)'

  const handleToggle = () => {
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
      >
        <span className="backend-dot" aria-hidden="true" />
        <span className="backend-label">{backend}</span>
      </button>
    </header>
  )
}
