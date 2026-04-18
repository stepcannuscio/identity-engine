import { useState } from 'react'
import { submitQueryFeedback } from '../../api/endpoints.js'
import { useAppState } from '../../store/appState.js'

const FEEDBACK_OPTIONS = [
  { value: 'helpful', label: 'Helpful' },
  { value: 'ungrounded', label: 'Ungrounded' },
  { value: 'missed_context', label: 'Missed context' },
  { value: 'wrong_focus', label: 'Wrong focus' },
]

export default function QueryFeedbackPanel({ message }) {
  const { addToast } = useAppState()
  const [selected, setSelected] = useState('')
  const [notes, setNotes] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isSubmitted, setIsSubmitted] = useState(false)
  const [error, setError] = useState('')

  if (!message?.metadata || !message?.query || isSubmitted) {
    return null
  }

  const handleSubmit = async () => {
    if (!selected) {
      setError('Choose one feedback label first.')
      return
    }

    setIsSaving(true)
    setError('')
    try {
      await submitQueryFeedback({
        query: message.query,
        response: message.content,
        feedback: selected,
        notes: notes.trim() || null,
        query_type: message.metadata.query_type,
        backend_used: message.metadata.backend_used,
        confidence: message.metadata.confidence,
        intent: message.metadata.intent ?? {
          source_profile: 'general',
          intent_tags: [],
          domain_hints: [],
        },
        domains_referenced: message.metadata.domains_referenced ?? [],
      })
      setIsSubmitted(true)
      addToast({ message: 'Query feedback saved locally.', tone: 'success', duration: 3000 })
    } catch (nextError) {
      setError(nextError?.response?.data?.detail ?? 'Unable to save feedback right now.')
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <section className="query-feedback-panel">
      <p className="query-feedback-title">Was this answer useful?</p>
      <div className="query-feedback-options">
        {FEEDBACK_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={selected === option.value ? 'button-primary' : 'button-secondary'}
            onClick={() => setSelected(option.value)}
            disabled={isSaving}
          >
            {option.label}
          </button>
        ))}
      </div>
      <textarea
        value={notes}
        onChange={(event) => setNotes(event.target.value)}
        className="query-feedback-notes"
        placeholder="Optional note about what worked or what was missing."
        disabled={isSaving}
      />
      <div className="query-feedback-actions">
        <button type="button" className="button-primary" onClick={handleSubmit} disabled={isSaving}>
          {isSaving ? 'Saving...' : 'Save feedback'}
        </button>
      </div>
      {error ? <p className="query-feedback-error">{error}</p> : null}
    </section>
  )
}
