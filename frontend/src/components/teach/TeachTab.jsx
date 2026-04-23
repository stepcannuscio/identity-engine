import { useState, useRef, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  analyzeArtifact,
  answerTeachQuestion,
  capture,
  capturePreview,
  feedbackTeachQuestion,
  getArtifactAnalysis,
  promoteArtifact,
  startReflection,
  submitReflectionTurn,
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

function candidateIds(items) {
  return new Set(items.map((item) => item.candidate_id))
}

export default function TeachTab({ bootstrapQuery }) {
  const queryClient = useQueryClient()
  const { addToast, backend, onboardingCompleted, providerStatuses } = useAppState()
  const [answer, setAnswer] = useState('')
  const [allowExternalAnswerExtraction, setAllowExternalAnswerExtraction] = useState(false)
  const [quickNote, setQuickNote] = useState('')
  const [allowExternalQuickNoteExtraction, setAllowExternalQuickNoteExtraction] = useState(false)
  const [artifactTitle, setArtifactTitle] = useState('')
  const [artifactText, setArtifactText] = useState('')
  const [artifactFile, setArtifactFile] = useState(null)
  const [artifactTags, setArtifactTags] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [reflectMode, setReflectMode] = useState(false)
  const [reflectSessionId, setReflectSessionId] = useState(null)
  const [reflectQuestion, setReflectQuestion] = useState(null)
  const [reflectAnswer, setReflectAnswer] = useState('')
  const [reflectHistory, setReflectHistory] = useState([])
  const [reflectSuggestions, setReflectSuggestions] = useState([])
  const [reflectThemes, setReflectThemes] = useState([])
  const [isReflecting, setIsReflecting] = useState(false)
  const [artifactId, setArtifactId] = useState(null)
  const [artifactAnalysis, setArtifactAnalysis] = useState(null)
  const [selectedAttributeIds, setSelectedAttributeIds] = useState(new Set())
  const [selectedPreferenceIds, setSelectedPreferenceIds] = useState(new Set())
  const [isPromoting, setIsPromoting] = useState(false)
  const pollRef = useRef(null)

  const bootstrap = bootstrapQuery.data
  const activeQuestion = bootstrap?.questions?.[0] ?? null
  const requiresExternalExtractionConsent = backend === 'external' || backend === 'private_server'
  const isPrivateServer = backend === 'private_server'
  const localAnalysisAvailable = providerStatuses.some(
    (provider) => provider.is_local && provider.available,
  )

  const TERMINAL_STATUSES = new Set(['analyzed', 'fallback_analyzed', 'failed'])
  const isAnalyzing =
    artifactAnalysis != null &&
    !TERMINAL_STATUSES.has(artifactAnalysis?.analysis_status)

  const stopPolling = () => {
    if (pollRef.current != null) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const startPolling = (id) => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const analysis = await getArtifactAnalysis(id)
        setAnalysisState(analysis)
        if (TERMINAL_STATUSES.has(analysis?.analysis_status)) {
          stopPolling()
          if (analysis.analysis_status === 'failed') {
            addToast({ message: analysis.analysis_warning ?? 'Analysis failed. You can retry.', tone: 'error' })
          } else {
            addToast({
              message:
                analysis.analysis_method === 'heuristic_fallback'
                  ? analysis.analysis_warning ?? 'Upload analyzed with a lightweight local fallback.'
                  : 'Upload analyzed locally.',
              tone: analysis.analysis_method === 'heuristic_fallback' ? 'warning' : 'success',
            })
          }
        }
      } catch {
        stopPolling()
      }
    }, 3000)
  }

  useEffect(() => stopPolling, [])

  const setAnalysisState = (analysis) => {
    setArtifactAnalysis(analysis)
    setSelectedAttributeIds(candidateIds(analysis?.candidate_attributes ?? []))
    setSelectedPreferenceIds(candidateIds(analysis?.candidate_preferences ?? []))
  }

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
      const response = await uploadArtifact({
        text: artifactFile ? null : artifactText,
        file: artifactFile,
        title: artifactTitle || null,
        type: 'document',
        source: 'upload',
        domain: activeQuestion?.domain ?? null,
        tags: parseTags(artifactTags),
      })
      setArtifactId(response.artifact_id)
      setArtifactTitle('')
      setArtifactText('')
      setArtifactFile(null)
      setArtifactTags('')
      setAnalysisState(null)
      await refreshBootstrap()
      addToast({ message: 'Artifact saved.', tone: 'success' })
      if (localAnalysisAvailable) {
        const analysis = await analyzeArtifact(response.artifact_id)
        setAnalysisState(analysis)
        if (!TERMINAL_STATUSES.has(analysis?.analysis_status)) {
          startPolling(response.artifact_id)
        } else if (analysis.analysis_status !== 'failed') {
          addToast({
            message:
              analysis.analysis_method === 'heuristic_fallback'
                ? analysis.analysis_warning ?? 'Upload analyzed with a lightweight local fallback.'
                : 'Upload analyzed locally.',
            tone: analysis.analysis_method === 'heuristic_fallback' ? 'warning' : 'success',
          })
        }
      }
    } catch (error) {
      addToast({ message: error?.response?.data?.detail ?? 'Unable to save that upload.' })
    } finally {
      setIsSaving(false)
    }
  }

  const handleAnalyzeArtifact = async () => {
    if (!artifactId) {
      return
    }
    try {
      const alreadyTerminal =
        TERMINAL_STATUSES.has(artifactAnalysis?.analysis_status) &&
        artifactAnalysis?.analysis_status !== 'failed'
      const analysis = alreadyTerminal
        ? await getArtifactAnalysis(artifactId)
        : await analyzeArtifact(artifactId)
      setAnalysisState(analysis)
      if (TERMINAL_STATUSES.has(analysis?.analysis_status)) {
        if (analysis.analysis_status !== 'failed') {
          addToast({
            message:
              analysis.analysis_method === 'heuristic_fallback'
                ? analysis.analysis_warning ?? 'Upload analyzed with a lightweight local fallback.'
                : 'Upload analyzed locally.',
            tone: analysis.analysis_method === 'heuristic_fallback' ? 'warning' : 'success',
          })
        }
      } else {
        startPolling(artifactId)
      }
    } catch (error) {
      addToast({ message: error?.response?.data?.detail ?? 'Unable to analyze that upload.' })
    }
  }

  const handlePromoteArtifact = async () => {
    if (!artifactId || !artifactAnalysis) {
      return
    }
    setIsPromoting(true)
    try {
      const response = await promoteArtifact(artifactId, {
        selected_attributes: artifactAnalysis.candidate_attributes.filter((item) =>
          selectedAttributeIds.has(item.candidate_id),
        ),
        selected_preferences: artifactAnalysis.candidate_preferences.filter((item) =>
          selectedPreferenceIds.has(item.candidate_id),
        ),
      })
      setAnalysisState(response.analysis)
      await refreshBootstrap()
      addToast({ message: 'Selected upload insights were promoted.', tone: 'success' })
    } catch (error) {
      addToast({ message: error?.response?.data?.detail ?? 'Unable to promote upload insights.' })
    } finally {
      setIsPromoting(false)
    }
  }

  const toggleSelection = (setter, current, id) => {
    const next = new Set(current)
    if (next.has(id)) {
      next.delete(id)
    } else {
      next.add(id)
    }
    setter(next)
  }

  const handleStartReflect = async () => {
    setIsReflecting(true)
    try {
      const response = await startReflection()
      setReflectSessionId(response.session_id)
      setReflectQuestion(response.first_question)
      setReflectHistory([{ role: 'assistant', content: response.first_question }])
      setReflectSuggestions([])
      setReflectThemes([])
      setReflectMode(true)
    } catch (error) {
      addToast({ message: error?.response?.data?.detail ?? 'Unable to start reflection.' })
    } finally {
      setIsReflecting(false)
    }
  }

  const handleReflectTurn = async () => {
    if (!reflectSessionId || !reflectAnswer.trim()) return
    setIsReflecting(true)
    const userText = reflectAnswer.trim()
    setReflectAnswer('')
    try {
      const response = await submitReflectionTurn(reflectSessionId, userText)
      setReflectHistory((prev) => [
        ...prev,
        { role: 'user', content: userText },
        { role: 'assistant', content: response.next_question },
      ])
      setReflectQuestion(response.next_question)
      if (response.suggested_updates?.length) {
        setReflectSuggestions((prev) => [...prev, ...response.suggested_updates])
      }
      if (response.themes_noticed?.length) {
        setReflectThemes((prev) => {
          const existing = new Set(prev)
          return [...prev, ...response.themes_noticed.filter((t) => !existing.has(t))]
        })
      }
    } catch (error) {
      addToast({ message: error?.response?.data?.detail ?? 'Unable to process that turn.' })
    } finally {
      setIsReflecting(false)
    }
  }

  const handleExitReflect = () => {
    setReflectMode(false)
    setReflectSessionId(null)
    setReflectQuestion(null)
    setReflectAnswer('')
    setReflectHistory([])
    setReflectSuggestions([])
    setReflectThemes([])
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

      {!reflectMode && onboardingCompleted ? (
        <div className="teach-action-row">
          <button
            type="button"
            className="button-secondary"
            onClick={handleStartReflect}
            disabled={isReflecting}
          >
            {isReflecting ? 'Starting...' : 'Deep Reflect'}
          </button>
        </div>
      ) : null}

      {reflectMode ? (
        <div className="teach-reflect">
          <div className="teach-panel-header">
            <h2>Deep Reflect</h2>
            <button type="button" className="button-ghost" onClick={handleExitReflect}>
              Exit
            </button>
          </div>
          <div className="teach-reflect-history">
            {reflectHistory.map((turn, i) => (
              <div
                key={i}
                className={`teach-reflect-turn teach-reflect-turn--${turn.role}`}
              >
                <span className="teach-reflect-role">
                  {turn.role === 'assistant' ? 'Reflect' : 'You'}
                </span>
                <p>{turn.content}</p>
              </div>
            ))}
          </div>
          {reflectQuestion ? (
            <div className="teach-reflect-input">
              <textarea
                value={reflectAnswer}
                onChange={(e) => setReflectAnswer(e.target.value)}
                placeholder="Respond in your own words…"
                disabled={isReflecting}
              />
              <div className="teach-action-row">
                <button
                  type="button"
                  className="button-primary"
                  onClick={handleReflectTurn}
                  disabled={isReflecting || !reflectAnswer.trim()}
                >
                  {isReflecting ? 'Thinking...' : 'Continue'}
                </button>
              </div>
            </div>
          ) : null}
          {reflectSuggestions.length ? (
            <div className="teach-analysis">
              <h3>Insights from this reflection</h3>
              <p className="field-help">
                These were noticed during your reflection. Review staged signals in Teach to
                accept or dismiss them.
              </p>
              {reflectSuggestions.map((s, i) => (
                <div key={i} className="field-help">
                  <strong>{s.label}</strong> ({s.domain}): {s.value}
                </div>
              ))}
            </div>
          ) : null}
          {reflectThemes.length ? (
            <div className="teach-analysis">
              <h3>Themes noticed</h3>
              <ul>
                {reflectThemes.map((t, i) => (
                  <li key={i} className="field-help">{t}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
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
                  {isPrivateServer
                    ? `I understand this raw answer will be sent to my private server for extraction.`
                    : 'I understand this raw answer may be sent to my configured external provider for extraction.'}
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
              {isPrivateServer
                ? `I understand this raw note will be sent to my private server for extraction.`
                : 'I understand this raw note may be sent to my configured external provider for extraction.'}
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
          <p className="field-help">
            Uploads stay as local searchable evidence first. After local analysis, you can
            promote reviewed facts or preferences into the source-of-truth store.
          </p>
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
                setArtifactId(null)
                setAnalysisState(null)
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
          {artifactId ? (
            <div className="teach-action-row">
              <button
                type="button"
                className="button-secondary"
                onClick={handleAnalyzeArtifact}
                disabled={!localAnalysisAvailable || isAnalyzing || isSaving}
              >
                {artifactAnalysis?.analysis_status === 'queued'
                  ? 'Queued...'
                  : isAnalyzing
                    ? 'Analyzing...'
                    : 'Analyze upload'}
              </button>
              {!localAnalysisAvailable ? (
                <span className="field-help">
                  Enable a local model to analyze uploads and promote grounded facts.
                </span>
              ) : null}
            </div>
          ) : null}
          {artifactAnalysis?.analysis_status === 'failed' ? (
            <div className="teach-analysis">
              <p className="field-help">
                <strong>Analysis failed:</strong>{' '}
                {artifactAnalysis.analysis_warning ?? 'An unexpected error occurred.'}
              </p>
              <div className="teach-action-row">
                <button
                  type="button"
                  className="button-secondary"
                  onClick={handleAnalyzeArtifact}
                  disabled={!localAnalysisAvailable || isSaving}
                >
                  Retry analysis
                </button>
              </div>
            </div>
          ) : null}
          {['analyzed', 'fallback_analyzed'].includes(artifactAnalysis?.analysis_status) ? (
            <div className="teach-analysis">
              <p className="field-help">
                <strong>Local summary:</strong> {artifactAnalysis.summary}
              </p>
              {artifactAnalysis.analysis_warning ? (
                <p className="field-help">
                  <strong>Note:</strong> {artifactAnalysis.analysis_warning}
                </p>
              ) : null}
              {artifactAnalysis.descriptor_tokens?.length ? (
                <p className="field-help">
                  <strong>Descriptors:</strong> {artifactAnalysis.descriptor_tokens.join(', ')}
                </p>
              ) : null}
              {artifactAnalysis.candidate_attributes?.length ? (
                <div className="teach-analysis-group">
                  <h3>Review candidate facts</h3>
                  {artifactAnalysis.candidate_attributes.map((item) => (
                    <label key={item.candidate_id} className="field-help">
                      <input
                        type="checkbox"
                        checked={selectedAttributeIds.has(item.candidate_id)}
                        disabled={item.status === 'promoted'}
                        onChange={() =>
                          toggleSelection(
                            setSelectedAttributeIds,
                            selectedAttributeIds,
                            item.candidate_id,
                          )
                        }
                      />{' '}
                      <strong>{item.label}</strong>: {item.value}
                      {item.status === 'promoted' ? ' (promoted)' : ''}
                    </label>
                  ))}
                </div>
              ) : null}
              {artifactAnalysis.candidate_preferences?.length ? (
                <div className="teach-analysis-group">
                  <h3>Review candidate preferences</h3>
                  {artifactAnalysis.candidate_preferences.map((item) => (
                    <label key={item.candidate_id} className="field-help">
                      <input
                        type="checkbox"
                        checked={selectedPreferenceIds.has(item.candidate_id)}
                        disabled={item.status === 'promoted'}
                        onChange={() =>
                          toggleSelection(
                            setSelectedPreferenceIds,
                            selectedPreferenceIds,
                            item.candidate_id,
                          )
                        }
                      />{' '}
                      <strong>{item.signal}</strong> {item.subject.replaceAll('_', ' ')}
                      {item.summary ? ` — ${item.summary}` : ''}
                      {item.status === 'promoted' ? ' (promoted)' : ''}
                    </label>
                  ))}
                </div>
              ) : null}
              <div className="teach-action-row">
                <button
                  type="button"
                  className="button-primary"
                  onClick={handlePromoteArtifact}
                  disabled={isPromoting || isSaving || isAnalyzing}
                >
                  {isPromoting ? 'Promoting...' : 'Promote selected'}
                </button>
              </div>
            </div>
          ) : null}
        </article>
      </div>
    </section>
  )
}
