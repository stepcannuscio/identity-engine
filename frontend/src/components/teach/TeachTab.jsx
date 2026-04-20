import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  answerTeachQuestion,
  capture,
  capturePreview,
  feedbackTeachQuestion,
  saveProviderCredentials,
  saveSetupProfile,
  uploadArtifact,
} from '../../api/endpoints.js'
import { useAppState } from '../../store/appState.js'

function toAcceptedItems(items) {
  return items.map((item) => ({
    domain: item.domain,
    label: item.label,
    value: item.value,
    elaboration: item.elaboration ?? null,
    mutability: item.mutability,
    confidence: item.confidence,
  }))
}

function parseTags(rawTags) {
  return rawTags
    .split(',')
    .map((tag) => tag.trim().toLowerCase())
    .filter(Boolean)
}

function computeRecommendedProfileCode(providers, privacyPreference) {
  const localReady = providers.some(
    (provider) =>
      provider.deployment === 'local' && provider.trust_boundary === 'self_hosted' && provider.available,
  )
  const externalReady = providers.some(
    (provider) => provider.deployment === 'external' && provider.available,
  )

  if (privacyPreference === 'privacy_first') {
    if (localReady) {
      return 'private_local_first'
    }
    return externalReady ? 'external_assist' : 'private_local_first'
  }

  if (privacyPreference === 'capability_first') {
    if (externalReady) {
      return 'external_assist'
    }
    return localReady ? 'private_local_first' : 'external_assist'
  }

  if (localReady && externalReady) {
    return 'balanced_hybrid'
  }
  if (localReady) {
    return 'private_local_first'
  }
  if (externalReady) {
    return 'external_assist'
  }
  return 'private_local_first'
}

function buildCredentialValues(providers) {
  return Object.fromEntries(
    providers.map((provider) => [
      provider.provider,
      Object.fromEntries((provider.credential_fields ?? []).map((field) => [field.name, ''])),
    ]),
  )
}

function buildProviderSelections(bootstrap) {
  return Object.fromEntries(
    (bootstrap?.profiles ?? []).map((profile) => {
      const preferredProvider = bootstrap?.preferred_provider
      const fallback =
        preferredProvider && profile.provider_options.includes(preferredProvider)
          ? preferredProvider
          : profile.recommended_provider ?? profile.provider_options?.[0] ?? null
      return [profile.code, fallback]
    }),
  )
}

function ProviderCard({
  provider,
  values,
  onFieldChange,
  onSave,
  isSaving,
  isSelected,
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

export default function TeachTab({ bootstrapQuery }) {
  const queryClient = useQueryClient()
  const {
    addToast,
    backend,
    activeProfile,
    onboardingCompleted,
    preferredProvider,
    securityPosture,
  } = useAppState()
  const [answer, setAnswer] = useState('')
  const [allowExternalAnswerExtraction, setAllowExternalAnswerExtraction] = useState(false)
  const [quickNote, setQuickNote] = useState('')
  const [allowExternalQuickNoteExtraction, setAllowExternalQuickNoteExtraction] = useState(false)
  const [artifactTitle, setArtifactTitle] = useState('')
  const [artifactText, setArtifactText] = useState('')
  const [artifactFile, setArtifactFile] = useState(null)
  const [artifactTags, setArtifactTags] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [privacyPreferenceDraft, setPrivacyPreferenceDraft] = useState('balanced')
  const [providerSelections, setProviderSelections] = useState({})
  const [credentialValues, setCredentialValues] = useState({})

  const bootstrap = bootstrapQuery.data
  const providers = bootstrap?.providers ?? []
  const posture = bootstrap?.security_posture ?? securityPosture
  const activeQuestion = bootstrap?.questions?.[0] ?? null
  const requiresExternalExtractionConsent = backend === 'external'
  const recommendedProfileCode = computeRecommendedProfileCode(
    providers,
    privacyPreferenceDraft,
  )
  const selectedProfileCode = activeProfile ?? recommendedProfileCode

  useEffect(() => {
    if (!bootstrap) {
      return
    }
    setPrivacyPreferenceDraft(bootstrap.privacy_preference ?? 'balanced')
    setProviderSelections(buildProviderSelections(bootstrap))
    setCredentialValues((current) => {
      if (Object.keys(current).length > 0) {
        return current
      }
      return buildCredentialValues(bootstrap.providers ?? [])
    })
  }, [bootstrap])

  const refreshBootstrap = async () => {
    await queryClient.invalidateQueries({ queryKey: ['teachBootstrap'] })
    await queryClient.invalidateQueries({ queryKey: ['attributes'] })
    await queryClient.invalidateQueries({ queryKey: ['domains'] })
  }

  const applyBootstrapUpdate = async (nextBootstrap) => {
    if (nextBootstrap) {
      queryClient.setQueryData(['teachBootstrap'], nextBootstrap)
      await queryClient.invalidateQueries({ queryKey: ['attributes'] })
      await queryClient.invalidateQueries({ queryKey: ['domains'] })
      return
    }
    await refreshBootstrap()
  }

  const handleProfileSave = async (profileCode, markComplete = false) => {
    const profile = bootstrap?.profiles?.find((item) => item.code === profileCode)
    const preferred =
      providerSelections[profileCode] ??
      profile?.recommended_provider ??
      profile?.provider_options?.[0] ??
      null

    setIsSaving(true)
    try {
      await saveSetupProfile({
        profile: profileCode,
        privacy_preference: privacyPreferenceDraft,
        preferred_provider: preferred,
        preferred_backend: profile?.default_backend ?? backend,
        onboarding_completed: markComplete,
      })
      await refreshBootstrap()
      addToast({
        message: markComplete ? 'Onboarding preferences saved.' : 'Configuration updated.',
        tone: 'success',
      })
    } catch (error) {
      addToast({
        message: error?.response?.data?.detail ?? 'Unable to save your configuration right now.',
      })
    } finally {
      setIsSaving(false)
    }
  }

  const handleProviderSave = async (provider) => {
    setIsSaving(true)
    try {
      await saveProviderCredentials(provider, credentialValues[provider] ?? {})
      setCredentialValues((current) => ({
        ...current,
        [provider]: Object.fromEntries(
          Object.keys(current[provider] ?? {}).map((fieldName) => [fieldName, '']),
        ),
      }))
      await refreshBootstrap()
      addToast({ message: `${provider} credentials saved.`, tone: 'success' })
    } catch (error) {
      addToast({
        message: error?.response?.data?.detail ?? `Unable to save ${provider} credentials.`,
      })
    } finally {
      setIsSaving(false)
    }
  }

  const handleAnswer = async () => {
    if (!activeQuestion || !answer.trim()) {
      return
    }
    setIsSaving(true)
    try {
      const response = await answerTeachQuestion(
        activeQuestion.id,
        requiresExternalExtractionConsent
          ? {
              answer,
              allow_external_extraction: allowExternalAnswerExtraction,
            }
          : { answer },
      )
      setAnswer('')
      setAllowExternalAnswerExtraction(false)
      await applyBootstrapUpdate(response?.next ?? null)
      addToast({ message: 'Answer saved.', tone: 'success' })
    } catch (error) {
      addToast({ message: error?.response?.data?.detail ?? 'Unable to save that answer.' })
    } finally {
      setIsSaving(false)
    }
  }

  const handleQuestionFeedback = async (feedback) => {
    if (!activeQuestion) {
      return
    }
    setIsSaving(true)
    try {
      const nextBootstrap = await feedbackTeachQuestion(activeQuestion.id, feedback)
      setAnswer('')
      setAllowExternalAnswerExtraction(false)
      await applyBootstrapUpdate(nextBootstrap ?? null)
      addToast({ message: 'Feedback saved.', tone: 'success' })
    } catch (error) {
      addToast({ message: error?.response?.data?.detail ?? 'Unable to save feedback.' })
    } finally {
      setIsSaving(false)
    }
  }

  const handleQuickNote = async () => {
    const text = quickNote.trim()
    if (!text) {
      return
    }
    setIsSaving(true)
    try {
      const preview = requiresExternalExtractionConsent
        ? await capturePreview(
            text,
            activeQuestion?.domain ?? null,
            allowExternalQuickNoteExtraction,
          )
        : await capturePreview(text, activeQuestion?.domain ?? null)
      if (!preview.proposed?.length) {
        addToast({ message: 'No attributes were extracted from that note yet.' })
        return
      }
      if (requiresExternalExtractionConsent) {
        await capture(
          text,
          activeQuestion?.domain ?? null,
          toAcceptedItems(preview.proposed),
          allowExternalQuickNoteExtraction,
        )
      } else {
        await capture(text, activeQuestion?.domain ?? null, toAcceptedItems(preview.proposed))
      }
      setQuickNote('')
      setAllowExternalQuickNoteExtraction(false)
      await refreshBootstrap()
      addToast({ message: 'Quick note saved.', tone: 'success' })
    } catch (error) {
      addToast({ message: error?.response?.data?.detail ?? 'Unable to save that note.' })
    } finally {
      setIsSaving(false)
    }
  }

  const handleArtifactUpload = async () => {
    if (!artifactFile && !artifactText.trim()) {
      return
    }
    setIsSaving(true)
    try {
      await uploadArtifact({
        text: artifactFile ? null : artifactText,
        file: artifactFile,
        title: artifactTitle || null,
        type: 'document',
        source: 'upload',
        domain: activeQuestion?.domain ?? null,
        tags: parseTags(artifactTags),
      })
      setArtifactTitle('')
      setArtifactText('')
      setArtifactFile(null)
      setArtifactTags('')
      await refreshBootstrap()
      addToast({ message: 'Artifact saved.', tone: 'success' })
    } catch (error) {
      addToast({ message: error?.response?.data?.detail ?? 'Unable to save that upload.' })
    } finally {
      setIsSaving(false)
    }
  }

  if (bootstrapQuery.isLoading) {
    return (
      <section className="teach-tab">
        <div className="screen-state">
          <p>Loading Teach...</p>
        </div>
      </section>
    )
  }

  return (
    <section className="teach-tab">
      <div className="teach-hero">
        <p className="eyebrow">{onboardingCompleted ? 'Teach' : 'Onboarding'}</p>
        <h1>{bootstrap?.cards?.[0]?.title ?? 'Teach the engine'}</h1>
        <p>{bootstrap?.cards?.[0]?.body ?? 'Share helpful information at your own pace.'}</p>
        {!onboardingCompleted ? (
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
        ) : null}
      </div>

      <div className="teach-grid">
        <article className="teach-panel">
          <div className="teach-panel-header">
            <h2>Privacy preference</h2>
            <span className="teach-status">{privacyPreferenceDraft.replace('_', ' ')}</span>
          </div>
          <div className="teach-profile-list">
            {bootstrap?.privacy_preferences?.map((option) => (
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
            <span className="teach-status">{activeProfile ?? 'not selected'}</span>
          </div>
          <div className="teach-profile-list">
            {bootstrap?.profiles?.map((profile) => {
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
                  className={`teach-profile-card ${activeProfile === profile.code ? 'active' : ''}`}
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
                    {activeProfile === profile.code ? 'Saved' : 'Use this configuration'}
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
                  <span className={`teach-check ${check.status}`}>{check.status}</span>
                </div>
                <p>{check.summary}</p>
                <p className="field-help">Recommended state: {check.recommended_value}</p>
                <p className="field-help">{check.recommendation}</p>
                {check.action_required ? (
                  <span className="teach-status pending">update recommended</span>
                ) : null}
              </div>
            ))}
          </div>
        </article>

        <article className="teach-panel teach-question-panel">
          <div className="teach-panel-header">
            <h2>Guided question</h2>
            <span className="teach-status">{activeQuestion?.source ?? 'ready'}</span>
          </div>
          {activeQuestion ? (
            <>
              <p className="teach-question">{activeQuestion.prompt}</p>
              <textarea
                value={answer}
                onChange={(event) => setAnswer(event.target.value)}
                placeholder="Answer in your own words."
              />
              {requiresExternalExtractionConsent ? (
                <label className="field-help">
                  <input
                    type="checkbox"
                    checked={allowExternalAnswerExtraction}
                    onChange={(event) =>
                      setAllowExternalAnswerExtraction(event.target.checked)
                    }
                  />{' '}
                  I understand this raw answer may be sent to my configured external provider
                  for extraction.
                </label>
              ) : null}
              <div className="teach-action-row">
                <button
                  type="button"
                  className="button-primary"
                  onClick={handleAnswer}
                  disabled={
                    isSaving ||
                    !answer.trim() ||
                    (requiresExternalExtractionConsent && !allowExternalAnswerExtraction)
                  }
                >
                  {isSaving ? 'Saving...' : 'Save answer'}
                </button>
                <button type="button" className="button-secondary" onClick={() => handleQuestionFeedback('skip')} disabled={isSaving}>
                  Skip
                </button>
              </div>
              <div className="teach-feedback-row">
                {['not_relevant', 'duplicate', 'already_covered', 'too_personal'].map((feedback) => (
                  <button
                    key={feedback}
                    type="button"
                    className="button-ghost"
                    onClick={() => handleQuestionFeedback(feedback)}
                    disabled={isSaving}
                  >
                    {feedback.replaceAll('_', ' ')}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p className="field-help">No question queued right now. Add a note or upload a document to keep teaching.</p>
          )}
        </article>

        <article className="teach-panel">
          <div className="teach-panel-header">
            <h2>Quick note</h2>
            <span className="teach-status">optional</span>
          </div>
          <textarea
            value={quickNote}
            onChange={(event) => setQuickNote(event.target.value)}
            placeholder="Share something useful in a few sentences."
          />
          {requiresExternalExtractionConsent ? (
            <label className="field-help">
              <input
                type="checkbox"
                checked={allowExternalQuickNoteExtraction}
                onChange={(event) =>
                  setAllowExternalQuickNoteExtraction(event.target.checked)
                }
              />{' '}
              I understand this raw note may be sent to my configured external provider for
              extraction.
            </label>
          ) : null}
          <div className="teach-action-row">
            <button
              type="button"
              className="button-primary"
              onClick={handleQuickNote}
              disabled={
                isSaving ||
                !quickNote.trim() ||
                (requiresExternalExtractionConsent && !allowExternalQuickNoteExtraction)
              }
            >
              Save note
            </button>
            <button
              type="button"
              className="button-secondary"
              onClick={() => {
                setQuickNote('')
                setAllowExternalQuickNoteExtraction(false)
              }}
              disabled={isSaving}
            >
              Skip
            </button>
          </div>
        </article>

        <article className="teach-panel">
          <div className="teach-panel-header">
            <h2>Upload file</h2>
            <span className="teach-status">taggable</span>
          </div>
          <input
            type="text"
            value={artifactTitle}
            onChange={(event) => setArtifactTitle(event.target.value)}
            placeholder="Document title"
          />
          <input
            type="text"
            value={artifactTags}
            onChange={(event) => setArtifactTags(event.target.value)}
            placeholder="Tags, comma separated"
          />
          <textarea
            value={artifactText}
            onChange={(event) => setArtifactText(event.target.value)}
            placeholder="Paste text here, or choose a file below."
          />
          <input
            type="file"
            accept=".txt,.md,.markdown,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(event) => setArtifactFile(event.target.files?.[0] ?? null)}
          />
          <div className="teach-action-row">
            <button
              type="button"
              className="button-primary"
              onClick={handleArtifactUpload}
              disabled={isSaving || (!artifactFile && !artifactText.trim())}
            >
              Save upload
            </button>
            <button
              type="button"
              className="button-secondary"
              onClick={() => {
                setArtifactTitle('')
                setArtifactTags('')
                setArtifactText('')
                setArtifactFile(null)
              }}
              disabled={isSaving}
            >
              Skip
            </button>
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
              isSaving={isSaving}
              isSelected={preferredProvider === provider.provider}
            />
          ))}
        </div>
      </div>
    </section>
  )
}
