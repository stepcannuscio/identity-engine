export default function ProviderCard({
  isSaving,
  isSelected,
  isTesting,
  testResult,
  onFieldChange,
  onSave,
  onTest,
  provider,
  values,
}) {
  const isUrlAuth = provider.auth_strategy === 'url'
  const isApiKeyAuth = provider.auth_strategy === 'api_key'

  return (
    <article className="teach-panel provider-card">
      <div className="teach-panel-header">
        <div className="teach-provider-title">
          <h3>{provider.label}</h3>
          <div className="teach-provider-meta">
            <span className={`teach-status ${provider.available ? 'ready' : 'pending'}`}>
              {provider.available ? 'ready' : 'needs setup'}
            </span>
            <span className="teach-status">{provider.trust_boundary.replace('_', ' ')}</span>
            {isSelected ? <span className="teach-status ready">selected</span> : null}
          </div>
        </div>
      </div>

      <p>{provider.description ?? provider.reason ?? `${provider.label} is configured.`}</p>
      {provider.model ? <p className="field-help">Default model: {provider.model}</p> : null}
      {provider.setup_hint ? <p className="field-help">{provider.setup_hint}</p> : null}
      {provider.reason && !provider.available ? <p className="field-help">{provider.reason}</p> : null}

      {(isApiKeyAuth || isUrlAuth)
        ? provider.credential_fields?.map((field) => (
            <input
              key={`${provider.provider}-${field.name}`}
              type={field.secret ? 'password' : field.input_type}
              value={values?.[field.name] ?? ''}
              onChange={(event) => onFieldChange(provider.provider, field.name, event.target.value)}
              placeholder={field.placeholder ?? field.label}
            />
          ))
        : null}

      {isApiKeyAuth ? (
        <button
          type="button"
          className="button-secondary"
          onClick={() => onSave(provider.provider)}
          disabled={
            isSaving ||
            provider.credential_fields.some((field) => !(values?.[field.name] ?? '').trim())
          }
        >
          {isSaving ? 'Saving...' : 'Save credentials'}
        </button>
      ) : null}

      {isUrlAuth ? (
        <div className="teach-action-row">
          <button
            type="button"
            className="button-secondary"
            onClick={() => onSave(provider.provider)}
            disabled={isSaving || !(values?.server_url ?? '').trim()}
          >
            {isSaving ? 'Saving...' : 'Save server URL'}
          </button>
          <button
            type="button"
            className="button-secondary"
            onClick={() => onTest(provider.provider)}
            disabled={isTesting || !(values?.server_url ?? '').trim()}
          >
            {isTesting ? 'Testing...' : 'Test connection'}
          </button>
        </div>
      ) : null}

      {testResult && isUrlAuth ? (
        <p className={`field-help ${testResult.reachable ? 'status-ready' : 'status-error'}`}>
          {testResult.reachable
            ? `Connected${testResult.latency_ms != null ? ` (${testResult.latency_ms}ms)` : ''} — ${testResult.model_available ? 'model ready' : testResult.error ?? 'model not found'}`
            : `Unreachable: ${testResult.error ?? 'check server and VPN connection'}`}
        </p>
      ) : null}
    </article>
  )
}
