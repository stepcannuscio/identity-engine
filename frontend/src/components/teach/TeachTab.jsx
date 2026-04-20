import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  answerTeachQuestion,
  capture,
  capturePreview,
  feedbackTeachQuestion,
  uploadArtifact,
} from '../../api/endpoints.js'
import SetupWorkspace from '../settings/SetupWorkspace.jsx'
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

export default function TeachTab({ bootstrapQuery }) {
  const queryClient = useQueryClient()
  const { addToast, backend, onboardingCompleted } = useAppState()
  const [answer, setAnswer] = useState('')
  const [allowExternalAnswerExtraction, setAllowExternalAnswerExtraction] = useState(false)
  const [quickNote, setQuickNote] = useState('')
  const [allowExternalQuickNoteExtraction, setAllowExternalQuickNoteExtraction] = useState(false)
  const [artifactTitle, setArtifactTitle] = useState('')
  const [artifactText, setArtifactText] = useState('')
  const [artifactFile, setArtifactFile] = useState(null)
  const [artifactTags, setArtifactTags] = useState('')
  const [isSaving, setIsSaving] = useState(false)

  const bootstrap = bootstrapQuery.data
  const activeQuestion = bootstrap?.questions?.[0] ?? null
  const requiresExternalExtractionConsent = backend === 'external'

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
        <p>
          {onboardingCompleted
            ? bootstrap?.cards?.[0]?.body ?? 'Share helpful information at your own pace.'
            : 'Finish your setup below, then keep teaching with guided answers, notes, and uploads.'}
        </p>
      </div>

      {!onboardingCompleted ? (
        <SetupWorkspace bootstrapQuery={bootstrapQuery} showOnboardingActions />
      ) : null}

      <div className="teach-grid">
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
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => handleQuestionFeedback('skip')}
                  disabled={isSaving}
                >
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
            <p className="field-help">
              No question queued right now. Add a note or upload a document to keep teaching.
            </p>
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
      </div>
    </section>
  )
}
