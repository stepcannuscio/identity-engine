import { useMemo, useState } from 'react'
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

function ProviderCard({ provider, value, onChange, onSave, isSaving }) {
  return (
    <article className="teach-panel provider-card">
      <div className="teach-panel-header">
        <h3>{provider.label}</h3>
        <span className={`teach-status ${provider.available ? 'ready' : 'pending'}`}>
          {provider.available ? 'ready' : 'needs setup'}
        </span>
      </div>
      <p className="field-help">{provider.reason ?? `${provider.label} is configured.`}</p>
      {!provider.is_local ? (
        <>
          <input
            type="password"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder={`Paste ${provider.label} API key`}
          />
          <button
            type="button"
            className="button-secondary"
            onClick={() => onSave(provider.provider)}
            disabled={!value.trim() || isSaving}
          >
            {isSaving ? 'Saving...' : 'Save key'}
          </button>
        </>
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
    providerStatuses,
    securityPosture,
  } = useAppState()
  const [answer, setAnswer] = useState('')
  const [quickNote, setQuickNote] = useState('')
  const [artifactTitle, setArtifactTitle] = useState('')
  const [artifactText, setArtifactText] = useState('')
  const [artifactFile, setArtifactFile] = useState(null)
  const [artifactTags, setArtifactTags] = useState('')
  const [anthropicKey, setAnthropicKey] = useState('')
  const [groqKey, setGroqKey] = useState('')
  const [isSaving, setIsSaving] = useState(false)

  const bootstrap = bootstrapQuery.data
  const recommendedProfile = useMemo(
    () => bootstrap?.profiles?.find((profile) => profile.recommended) ?? null,
    [bootstrap?.profiles],
  )
  const activeQuestion = bootstrap?.questions?.[0] ?? null

  const refreshBootstrap = async () => {
    await queryClient.invalidateQueries({ queryKey: ['teachBootstrap'] })
    await queryClient.invalidateQueries({ queryKey: ['attributes'] })
    await queryClient.invalidateQueries({ queryKey: ['domains'] })
  }

  const handleProfileSave = async (profileCode, markComplete = false) => {
    setIsSaving(true)
    try {
      await saveSetupProfile({
        profile: profileCode,
        preferred_backend:
          bootstrap?.profiles?.find((profile) => profile.code === profileCode)?.default_backend ??
          backend,
        onboarding_completed: markComplete,
      })
      await refreshBootstrap()
      addToast({
        message: markComplete ? 'Onboarding preferences saved.' : 'Profile updated.',
        tone: 'success',
      })
    } catch (error) {
      addToast({
        message: error?.response?.data?.detail ?? 'Unable to save your profile right now.',
      })
    } finally {
      setIsSaving(false)
    }
  }

  const handleProviderSave = async (provider) => {
    const value = provider === 'anthropic' ? anthropicKey : groqKey
    setIsSaving(true)
    try {
      await saveProviderCredentials(provider, value)
      if (provider === 'anthropic') {
        setAnthropicKey('')
      } else {
        setGroqKey('')
      }
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
      await answerTeachQuestion(activeQuestion.id, { answer })
      setAnswer('')
      await refreshBootstrap()
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
      await feedbackTeachQuestion(activeQuestion.id, feedback)
      setAnswer('')
      await refreshBootstrap()
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
      const preview = await capturePreview(text, activeQuestion?.domain ?? null)
      if (!preview.proposed?.length) {
        addToast({ message: 'No attributes were extracted from that note yet.' })
        return
      }
      await capture(text, activeQuestion?.domain ?? null, toAcceptedItems(preview.proposed))
      setQuickNote('')
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
              onClick={() => handleProfileSave(activeProfile ?? recommendedProfile?.code ?? 'private_local_first', true)}
              disabled={isSaving}
            >
              Finish onboarding
            </button>
            <button type="button" className="button-secondary">
              Finish later
            </button>
          </div>
        ) : null}
      </div>

      <div className="teach-grid">
        <article className="teach-panel">
          <div className="teach-panel-header">
            <h2>Privacy profile</h2>
            <span className="teach-status">{activeProfile ?? 'not selected'}</span>
          </div>
          <div className="teach-profile-list">
            {bootstrap?.profiles?.map((profile) => (
              <button
                key={profile.code}
                type="button"
                className={`teach-profile-card ${activeProfile === profile.code ? 'active' : ''}`}
                onClick={() => handleProfileSave(profile.code)}
                disabled={!profile.available || isSaving}
              >
                <strong>{profile.label}</strong>
                <span>{profile.description}</span>
                {profile.recommended ? <em>Recommended</em> : null}
              </button>
            ))}
          </div>
        </article>

        <article className="teach-panel">
          <div className="teach-panel-header">
            <h2>Security recommendations</h2>
            <span className="teach-status">
              {securityPosture?.supported ? securityPosture.platform : 'manual review'}
            </span>
          </div>
          <div className="teach-security-list">
            {securityPosture?.checks?.map((check) => (
              <div key={check.code} className="teach-security-item">
                <strong>{check.label}</strong>
                <span className={`teach-check ${check.status}`}>{check.status}</span>
                <p>{check.summary}</p>
                <p className="field-help">{check.recommendation}</p>
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
              <div className="teach-action-row">
                <button type="button" className="button-primary" onClick={handleAnswer} disabled={isSaving || !answer.trim()}>
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
          <div className="teach-action-row">
            <button type="button" className="button-primary" onClick={handleQuickNote} disabled={isSaving || !quickNote.trim()}>
              Save note
            </button>
            <button type="button" className="button-secondary" onClick={() => setQuickNote('')} disabled={isSaving}>
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
          {providerStatuses.map((provider) => (
            <ProviderCard
              key={provider.provider}
              provider={provider}
              value={provider.provider === 'anthropic' ? anthropicKey : groqKey}
              onChange={provider.provider === 'anthropic' ? setAnthropicKey : setGroqKey}
              onSave={handleProviderSave}
              isSaving={isSaving}
            />
          ))}
        </div>
      </div>
    </section>
  )
}
