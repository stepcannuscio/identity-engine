import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  capture,
  capturePreview,
  createPreferenceSignal,
  previewInterview,
  saveInterview,
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

function ActionCard({ suggestion, onSuccess }) {
  const { addToast } = useAppState()
  const queryClient = useQueryClient()
  const [isOpen, setIsOpen] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState('')
  const [captureText, setCaptureText] = useState('')
  const [preferenceSubject, setPreferenceSubject] = useState(suggestion.action.subject ?? '')
  const [preferenceSignal, setPreferenceSignal] = useState(suggestion.action.signal ?? 'prefer')
  const [interviewAnswer, setInterviewAnswer] = useState('')
  const [artifactTitle, setArtifactTitle] = useState(suggestion.action.title ?? '')
  const [artifactText, setArtifactText] = useState('')
  const [artifactFile, setArtifactFile] = useState(null)

  const finishSuccess = async (message) => {
    await queryClient.invalidateQueries({ queryKey: ['attributes'] })
    await queryClient.invalidateQueries({ queryKey: ['domains'] })
    addToast({ message, tone: 'success', duration: 4000 })
    setError('')
    setIsOpen(false)
    setCaptureText('')
    setInterviewAnswer('')
    setArtifactText('')
    setArtifactFile(null)
    onSuccess?.()
  }

  const handleIdentityCapture = async () => {
    const text = captureText.trim()
    if (!text) {
      setError('Add a quick note before saving.')
      return
    }

    setIsSaving(true)
    setError('')
    try {
      const preview = await capturePreview(text, suggestion.action.domain_hint ?? null)
      if (!preview.proposed?.length) {
        setError('No attributes were extracted from that note yet.')
        return
      }
      await capture(
        text,
        suggestion.action.domain_hint ?? null,
        toAcceptedItems(preview.proposed),
      )
      await finishSuccess('Quick capture saved.')
    } catch (nextError) {
      setError(nextError?.response?.data?.detail ?? 'Unable to save quick capture right now.')
    } finally {
      setIsSaving(false)
    }
  }

  const handlePreferenceCapture = async () => {
    const subject = preferenceSubject.trim()
    if (!subject) {
      setError('Add a preference before saving.')
      return
    }

    setIsSaving(true)
    setError('')
    try {
      await createPreferenceSignal({
        category: suggestion.action.category ?? 'general',
        subject,
        signal: preferenceSignal,
        strength: Number(suggestion.action.strength ?? 3),
        source: 'explicit_feedback',
      })
      await finishSuccess('Preference saved.')
    } catch (nextError) {
      setError(nextError?.response?.data?.detail ?? 'Unable to save preference right now.')
    } finally {
      setIsSaving(false)
    }
  }

  const handleInterviewAnswer = async () => {
    const answer = interviewAnswer.trim()
    if (!answer) {
      setError('Add an answer before saving.')
      return
    }

    setIsSaving(true)
    setError('')
    try {
      const preview = await previewInterview(
        suggestion.action.domain,
        suggestion.action.question,
        answer,
      )
      if (!preview.proposed?.length) {
        setError('No attributes were extracted from that answer yet.')
        return
      }
      await saveInterview(
        suggestion.action.domain,
        suggestion.action.question,
        answer,
        toAcceptedItems(preview.proposed),
      )
      await finishSuccess('Interview answer saved.')
    } catch (nextError) {
      setError(nextError?.response?.data?.detail ?? 'Unable to save interview answer right now.')
    } finally {
      setIsSaving(false)
    }
  }

  const handleArtifactUpload = async () => {
    const text = artifactText.trim()
    if (!text && !artifactFile) {
      setError('Add a note or choose a file before saving.')
      return
    }

    setIsSaving(true)
    setError('')
    try {
      await uploadArtifact({
        text: artifactFile ? null : text,
        file: artifactFile,
        title: artifactTitle.trim() || suggestion.action.title || null,
        type: suggestion.action.type ?? 'note',
        source: suggestion.action.source ?? 'upload',
        domain: suggestion.action.domain ?? null,
      })
      await finishSuccess('Artifact saved.')
    } catch (nextError) {
      setError(nextError?.response?.data?.detail ?? 'Unable to save artifact right now.')
    } finally {
      setIsSaving(false)
    }
  }

  const renderForm = () => {
    if (!isOpen) {
      return null
    }

    if (suggestion.kind === 'quick_capture' && suggestion.action.target === 'attribute') {
      return (
        <div className="acquisition-form">
          <textarea
            value={captureText}
            onChange={(event) => setCaptureText(event.target.value)}
            placeholder={suggestion.action.placeholder ?? 'Share a quick note.'}
          />
          <div className="acquisition-actions">
            <button type="button" className="button-primary" onClick={handleIdentityCapture} disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save quick note'}
            </button>
          </div>
        </div>
      )
    }

    if (suggestion.kind === 'quick_capture' && suggestion.action.target === 'preference_signal') {
      return (
        <div className="acquisition-form">
          <input
            type="text"
            value={preferenceSubject}
            onChange={(event) => setPreferenceSubject(event.target.value)}
            placeholder={suggestion.action.placeholder ?? 'Add a preference.'}
          />
          <select
            value={preferenceSignal}
            onChange={(event) => setPreferenceSignal(event.target.value)}
          >
            <option value="prefer">Prefer</option>
            <option value="like">Like</option>
            <option value="accept">Accept</option>
            <option value="avoid">Avoid</option>
            <option value="dislike">Dislike</option>
            <option value="reject">Reject</option>
          </select>
          <div className="acquisition-actions">
            <button type="button" className="button-primary" onClick={handlePreferenceCapture} disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save preference'}
            </button>
          </div>
        </div>
      )
    }

    if (suggestion.kind === 'interview_question') {
      return (
        <div className="acquisition-form">
          <textarea
            value={interviewAnswer}
            onChange={(event) => setInterviewAnswer(event.target.value)}
            placeholder={suggestion.action.placeholder ?? 'Answer in your own words.'}
          />
          <div className="acquisition-actions">
            <button type="button" className="button-primary" onClick={handleInterviewAnswer} disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save answer'}
            </button>
          </div>
        </div>
      )
    }

    if (suggestion.kind === 'artifact_upload') {
      return (
        <div className="acquisition-form">
          <input
            type="text"
            value={artifactTitle}
            onChange={(event) => setArtifactTitle(event.target.value)}
            placeholder="Artifact title"
          />
          <textarea
            value={artifactText}
            onChange={(event) => setArtifactText(event.target.value)}
            placeholder={suggestion.action.placeholder ?? 'Paste a note here.'}
          />
          <input
            type="file"
            accept=".txt,.md,.markdown,text/plain,text/markdown"
            onChange={(event) => setArtifactFile(event.target.files?.[0] ?? null)}
          />
          <div className="acquisition-actions">
            <button type="button" className="button-primary" onClick={handleArtifactUpload} disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save artifact'}
            </button>
          </div>
        </div>
      )
    }

    return null
  }

  const buttonLabel = {
    quick_capture:
      suggestion.action.target === 'preference_signal' ? 'Add preference' : 'Open capture',
    interview_question: 'Answer question',
    artifact_upload: 'Upload note',
  }[suggestion.kind]

  return (
    <article className="acquisition-card">
      <p className="acquisition-prompt">{suggestion.prompt}</p>
      <button
        type="button"
        className="button-secondary"
        onClick={() => {
          setError('')
          setIsOpen((current) => !current)
        }}
      >
        {isOpen ? 'Hide' : buttonLabel}
      </button>
      {renderForm()}
      {error ? <p className="acquisition-error">{error}</p> : null}
    </article>
  )
}

export default function AcquisitionPanel({ acquisition }) {
  if (!acquisition || acquisition.status !== 'suggested' || !acquisition.suggestions?.length) {
    return null
  }

  return (
    <section className="acquisition-panel">
      <p className="acquisition-title">Next best input</p>
      <div className="acquisition-list">
        {acquisition.suggestions.map((suggestion, index) => (
          <ActionCard key={`${suggestion.kind}-${index}-${suggestion.prompt}`} suggestion={suggestion} />
        ))}
      </div>
    </section>
  )
}
