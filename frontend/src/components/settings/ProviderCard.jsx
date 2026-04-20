export default function ProviderCard({
  isSaving,
  isSelected,
  onFieldChange,
  onSave,
  provider,
  values,
}) {
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

      {provider.auth_strategy === 'api_key'
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

      {provider.auth_strategy === 'api_key' ? (
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
    </article>
  )
}
