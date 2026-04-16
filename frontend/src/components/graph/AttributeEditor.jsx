import { useEffect, useMemo, useState } from 'react'
import {
  createAttribute,
  retractAttribute,
  updateAttribute,
} from '../../api/endpoints.js'

const PROTECTED_DOMAINS = new Set([
  'beliefs',
  'fears',
  'relationships',
  'patterns',
])

function buildInitialState(attribute) {
  return {
    domain: attribute?.domain ?? 'personality',
    label: attribute?.label ?? '',
    value: attribute?.value ?? '',
    elaboration: attribute?.elaboration ?? '',
    confidence: attribute?.confidence ?? 0.5,
    mutability: attribute?.mutability ?? 'evolving',
    routing: attribute?.routing ?? 'local_only',
  }
}

export default function AttributeEditor({
  attribute,
  domains,
  isOpen,
  onClose,
  onSaved,
}) {
  const [form, setForm] = useState(buildInitialState(attribute))
  const [error, setError] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)

  const isCreating = !attribute?.id
  const isProtectedDomain = useMemo(
    () => PROTECTED_DOMAINS.has(form.domain),
    [form.domain],
  )

  useEffect(() => {
    if (!isOpen) {
      return
    }
    setForm(buildInitialState(attribute))
    setError('')
  }, [attribute, isOpen])

  useEffect(() => {
    if (isProtectedDomain && form.routing !== 'local_only') {
      setForm((current) => ({ ...current, routing: 'local_only' }))
    }
  }, [form.routing, isProtectedDomain])

  if (!isOpen) {
    return null
  }

  const handleSave = async () => {
    if (!form.value.trim()) {
      setError('Value is required.')
      return
    }

    if (isCreating && !form.label.trim()) {
      setError('Label is required.')
      return
    }

    setIsSaving(true)
    setError('')

    try {
      if (isCreating) {
        await createAttribute({
          domain: form.domain,
          label: form.label.trim(),
          value: form.value.trim(),
          elaboration: form.elaboration.trim() || null,
          confidence: Number(form.confidence),
          mutability: form.mutability,
          routing: form.routing,
          source: 'explicit',
        })
      } else {
        await updateAttribute(attribute.id, {
          value: form.value.trim(),
          elaboration: form.elaboration.trim() || null,
          confidence: Number(form.confidence),
          mutability: form.mutability,
          routing: form.routing,
        })
      }

      await onSaved()
    } catch (requestError) {
      setError(requestError.response?.data?.message || 'Unable to save attribute.')
    } finally {
      setIsSaving(false)
    }
  }

  const handleRetract = async () => {
    if (!attribute?.id) {
      return
    }

    if (!window.confirm('Retract this attribute?')) {
      return
    }

    setIsDeleting(true)
    setError('')

    try {
      await retractAttribute(attribute.id)
      await onSaved()
    } catch {
      setError('Unable to retract attribute.')
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <div className="editor-backdrop" onClick={onClose} role="presentation">
      <div
        className="editor-modal"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="editor-header">
          <div>
            <p className="eyebrow">{isCreating ? 'Create attribute' : 'Edit attribute'}</p>
            <h2 className="editor-title">
              {isCreating ? 'New attribute' : attribute.label.replaceAll('_', ' ')}
            </h2>
          </div>
          <button type="button" className="text-button" onClick={onClose}>
            close
          </button>
        </div>

        <div className="editor-grid">
          {isCreating ? (
            <>
              <div>
                <label className="field-label" htmlFor="attribute-domain">
                  Domain
                </label>
                <select
                  id="attribute-domain"
                  value={form.domain}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, domain: event.target.value }))
                  }
                >
                  {domains.map((domain) => (
                    <option key={domain.domain} value={domain.domain}>
                      {domain.domain}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="field-label" htmlFor="attribute-label">
                  Label
                </label>
                <input
                  id="attribute-label"
                  value={form.label}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, label: event.target.value }))
                  }
                  placeholder="response_to_uncertainty"
                />
              </div>
            </>
          ) : null}

          <div>
            <label className="field-label" htmlFor="attribute-value">
              Value
            </label>
            <textarea
              id="attribute-value"
              rows={4}
              value={form.value}
              onChange={(event) =>
                setForm((current) => ({ ...current, value: event.target.value }))
              }
            />
          </div>

          <div>
            <label className="field-label" htmlFor="attribute-elaboration">
              Elaboration
            </label>
            <textarea
              id="attribute-elaboration"
              rows={3}
              value={form.elaboration}
              onChange={(event) =>
                setForm((current) => ({ ...current, elaboration: event.target.value }))
              }
            />
          </div>

          <div>
            <label className="field-label" htmlFor="attribute-confidence">
              Confidence ({Number(form.confidence).toFixed(2)})
            </label>
            <input
              id="attribute-confidence"
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={form.confidence}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  confidence: Number(event.target.value),
                }))
              }
            />
          </div>

          <div>
            <p className="field-label">Mutability</p>
            <div className="toggle-group">
              {['stable', 'evolving'].map((option) => (
                <button
                  key={option}
                  type="button"
                  className={`toggle-button ${
                    form.mutability === option ? 'active' : ''
                  }`}
                  onClick={() =>
                    setForm((current) => ({ ...current, mutability: option }))
                  }
                >
                  {option}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="field-label">Routing</p>
            <div
              className="toggle-group"
              title={
                isProtectedDomain
                  ? 'Protected domains cannot be marked external ok.'
                  : undefined
              }
            >
              {[
                ['local_only', 'local only'],
                ['external_ok', 'external ok'],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={`toggle-button ${form.routing === value ? 'active' : ''}`}
                  disabled={value === 'external_ok' && isProtectedDomain}
                  onClick={() =>
                    setForm((current) => ({ ...current, routing: value }))
                  }
                >
                  {label}
                </button>
              ))}
            </div>
            {form.routing === 'external_ok' ? (
              <p className="routing-warning">
                This attribute may be included in prompts sent to external APIs
                when external mode is active.
              </p>
            ) : null}
          </div>

          {error ? (
            <p className="field-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>

        <div className="editor-footer">
          <button
            type="button"
            className="button-primary"
            onClick={handleSave}
            disabled={isSaving || isDeleting}
          >
            Save
          </button>
          <button
            type="button"
            className="button-secondary"
            onClick={onClose}
            disabled={isSaving || isDeleting}
          >
            Cancel
          </button>
          {!isCreating ? (
            <button
              type="button"
              className="button-danger"
              onClick={handleRetract}
              disabled={isSaving || isDeleting}
            >
              Retract
            </button>
          ) : null}
        </div>
      </div>
    </div>
  )
}
