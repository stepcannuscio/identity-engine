import ProviderCard from './ProviderCard.jsx'
import { useSetupWorkspace } from '../../hooks/useSetupWorkspace.js'

export default function SetupWorkspace({ bootstrapQuery, showOnboardingActions = false }) {
  const {
    bootstrap,
    credentialValues,
    handleProfileSave,
    handleProviderSave,
    handleProviderTest,
    handleSecurityCheckComplete,
    isSaving,
    isTesting,
    pendingSecurityCode,
    posture,
    privacyPreferenceDraft,
    providers,
    providerSelections,
    recommendedProfileCode,
    savedProfile,
    savedProvider,
    selectedProfileCode,
    setCredentialValues,
    setPrivacyPreferenceDraft,
    setProviderSelections,
    testResults,
  } = useSetupWorkspace({ bootstrapQuery })

  if (!bootstrap) {
    return null
  }

  return (
    <div className="teach-grid">
      {showOnboardingActions ? (
        <article className="teach-panel">
          <div className="teach-panel-header">
            <h2>Finish setup</h2>
            <span className="teach-status">{selectedProfileCode.replaceAll('_', ' ')}</span>
          </div>
          <p>Choose your privacy preference and configuration, then finish onboarding when you're ready.</p>
          <div className="teach-hero-actions">
            <button
              type="button"
              className="button-primary"
              onClick={() => handleProfileSave(selectedProfileCode, true)}
              disabled={isSaving}
            >
              Finish onboarding
            </button>
            <button
              type="button"
              className="button-secondary"
              onClick={() => handleProfileSave(selectedProfileCode, false)}
              disabled={isSaving}
            >
              Save and continue later
            </button>
          </div>
        </article>
      ) : null}

      <article className="teach-panel">
        <div className="teach-panel-header">
          <h2>Privacy preference</h2>
          <span className="teach-status">{privacyPreferenceDraft.replace('_', ' ')}</span>
        </div>
        <div className="teach-profile-list">
          {bootstrap.privacy_preferences?.map((option) => (
            <button
              key={option.code}
              type="button"
              className={`teach-profile-card ${privacyPreferenceDraft === option.code ? 'active' : ''}`}
              onClick={() => setPrivacyPreferenceDraft(option.code)}
              disabled={isSaving}
            >
              <strong>{option.label}</strong>
              <span>{option.description}</span>
            </button>
          ))}
        </div>
      </article>

      <article className="teach-panel">
        <div className="teach-panel-header">
          <h2>Recommended configurations</h2>
          <span className="teach-status">{savedProfile ?? 'not selected'}</span>
        </div>
        <div className="teach-profile-list">
          {bootstrap.profiles?.map((profile) => {
            const compatibleProviders = providers.filter((provider) =>
              profile.provider_options.includes(provider.provider),
            )
            const isRecommended = profile.code === recommendedProfileCode
            const selectedProvider =
              providerSelections[profile.code] ??
              profile.recommended_provider ??
              profile.provider_options?.[0] ??
              ''

            return (
              <div
                key={profile.code}
                className={`teach-profile-card ${savedProfile === profile.code ? 'active' : ''}`}
              >
                <div className="teach-profile-header">
                  <strong>{profile.label}</strong>
                  {isRecommended ? <em>Recommended</em> : null}
                </div>
                <span>{profile.description}</span>
                <p className="field-help">{profile.recommendation_reason}</p>
                <div className="teach-inline-pills">
                  <span className="teach-status">{profile.provider_scope.replaceAll('_', ' ')}</span>
                  <span className="teach-status">{profile.default_backend} default</span>
                </div>
                {compatibleProviders.length ? (
                  <div className="teach-inline-pills">
                    {compatibleProviders.map((provider) => (
                      <span key={`${profile.code}-${provider.provider}`} className="teach-status">
                        {provider.label}
                      </span>
                    ))}
                  </div>
                ) : null}
                {profile.provider_options?.length > 0 ? (
                  <label className="teach-field">
                    <span>Provider for this configuration</span>
                    <select
                      value={selectedProvider}
                      onChange={(event) =>
                        setProviderSelections((current) => ({
                          ...current,
                          [profile.code]: event.target.value,
                        }))
                      }
                      disabled={isSaving || profile.provider_options.length === 1}
                    >
                      {profile.provider_options.map((providerId) => {
                        const provider = providers.find((item) => item.provider === providerId)
                        return (
                          <option key={`${profile.code}-${providerId}`} value={providerId}>
                            {provider?.label ?? providerId}
                          </option>
                        )
                      })}
                    </select>
                  </label>
                ) : null}
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => handleProfileSave(profile.code)}
                  disabled={!profile.available || isSaving}
                >
                  {savedProfile === profile.code ? 'Saved' : 'Use this configuration'}
                </button>
                {!profile.available ? (
                  <p className="field-help">
                    {profile.requires_external_provider
                      ? 'Set up a compatible external provider below to enable this option.'
                      : 'This option is unavailable on the current machine right now.'}
                  </p>
                ) : null}
              </div>
            )
          })}
        </div>
      </article>

      <article className="teach-panel">
        <div className="teach-panel-header">
          <h2>Security recommendations</h2>
          <span className="teach-status">
            {posture?.supported ? posture.platform : 'manual review'}
          </span>
        </div>
        <div className="teach-security-list">
          {posture?.checks?.map((check) => (
            <div key={check.code} className="teach-security-item">
              <div className="teach-panel-header">
                <strong>{check.label}</strong>
                <div className="teach-inline-pills">
                  <span className={`teach-check ${check.status}`}>{check.status}</span>
                  {check.user_marked_complete ? (
                    <span className="teach-status ready">marked complete</span>
                  ) : null}
                </div>
              </div>
              <p>{check.summary}</p>
              <p className="field-help">Recommended state: {check.recommended_value}</p>
              <p className="field-help">{check.recommendation}</p>
              {check.status === 'unknown' ? (
                <div className="teach-action-row">
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={() =>
                      handleSecurityCheckComplete(check.code, !check.user_marked_complete)
                    }
                    disabled={pendingSecurityCode === check.code}
                  >
                    {pendingSecurityCode === check.code
                      ? 'Saving...'
                      : check.user_marked_complete
                        ? 'Mark incomplete'
                        : 'Mark complete'}
                  </button>
                </div>
              ) : null}
              {check.action_required ? (
                <span className="teach-status pending">update recommended</span>
              ) : null}
            </div>
          ))}
        </div>
      </article>

      <div className="teach-provider-grid">
        {providers.map((provider) => (
          <ProviderCard
            key={provider.provider}
            provider={provider}
            values={credentialValues[provider.provider] ?? {}}
            onFieldChange={(providerId, fieldName, value) =>
              setCredentialValues((current) => ({
                ...current,
                [providerId]: {
                  ...(current[providerId] ?? {}),
                  [fieldName]: value,
                },
              }))
            }
            onSave={handleProviderSave}
            onTest={handleProviderTest}
            isSaving={isSaving}
            isTesting={isTesting}
            testResult={testResults[provider.provider] ?? null}
            isSelected={savedProvider === provider.provider}
          />
        ))}
      </div>
    </div>
  )
}
