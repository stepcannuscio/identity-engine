import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { capture, capturePreview } from '../../api/endpoints.js'

function PreviewCard({ item, onToggle }) {
  return (
    <article className={`capture-preview-card ${item.accepted ? '' : 'rejected'}`}>
      <div className="capture-preview-top">
        <div>
          <h4 className="capture-preview-title">
            {item.label.replaceAll('_', ' ')}
          </h4>
          <p className="attribute-card-secondary">{item.domain}</p>
        </div>
        <div className="toggle-group">
          <button
            type="button"
            className={`toggle-button ${item.accepted ? 'active' : ''}`}
            onClick={() => onToggle(true)}
          >
            Accept
          </button>
          <button
            type="button"
            className={`toggle-button ${!item.accepted ? 'active' : ''}`}
            onClick={() => onToggle(false)}
          >
            Reject
          </button>
        </div>
      </div>
      <p className="attribute-card-value">{item.value}</p>
      {item.elaboration ? (
        <p className="attribute-card-secondary">{item.elaboration}</p>
      ) : null}
      {item.conflicts_with ? (
        <p className="field-help">
          Conflicts with existing attribute: {item.conflicts_with.label}
        </p>
      ) : null}
    </article>
  )
}

export default function CapturePanel({ domains, onSaved }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [domainHint, setDomainHint] = useState('')
  const [previewItems, setPreviewItems] = useState([])
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [isPreviewing, setIsPreviewing] = useState(false)
  const [isSaving, setIsSaving] = useState(false)

  const acceptedItems = previewItems
    .filter((item) => item.accepted)
    .map((item) => ({
      domain: item.domain,
      label: item.label,
      value: item.value,
      elaboration: item.elaboration,
      mutability: item.mutability,
      confidence: item.confidence,
    }))

  const handlePreview = async () => {
    if (!text.trim()) {
      setError('Enter a note to preview.')
      return
    }

    setError('')
    setSuccess('')
    setIsPreviewing(true)

    try {
      const response = await capturePreview(text, domainHint)
      setPreviewItems(
        response.proposed.map((item) => ({
          ...item,
          accepted: true,
        })),
      )
    } catch {
      setError('Unable to preview capture right now.')
    } finally {
      setIsPreviewing(false)
    }
  }

  const handleSave = async () => {
    if (!acceptedItems.length) {
      setError('Select at least one attribute to save.')
      return
    }

    setError('')
    setIsSaving(true)

    try {
      const response = await capture(text, domainHint, acceptedItems)
      await onSaved()
      setSuccess(`Saved ${response.attributes_saved} attribute(s)`)
      setPreviewItems([])
      setText('')
      setDomainHint('')
      setOpen(false)
    } catch {
      setError('Unable to save accepted attributes.')
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <section className="capture-panel">
      <button
        type="button"
        className="capture-toggle"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="capture-title">+ Quick capture</span>
        {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>

      {success ? <p className="success-message">{success}</p> : null}

      {open ? (
        <div className="capture-body">
          <div>
            <label className="field-label" htmlFor="capture-text">
              What&apos;s on your mind?
            </label>
            <textarea
              id="capture-text"
              rows={5}
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </div>

          <div className="capture-actions">
            <select
              value={domainHint}
              onChange={(event) => setDomainHint(event.target.value)}
            >
              <option value="">Any domain</option>
              {domains.map((domain) => (
                <option key={domain.domain} value={domain.domain}>
                  {domain.domain}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="button-secondary"
              onClick={handlePreview}
              disabled={isPreviewing}
            >
              Preview
            </button>
          </div>

          {error ? (
            <p className="field-error" role="alert">
              {error}
            </p>
          ) : null}

          {previewItems.length ? (
            <>
              <div className="capture-preview-list">
                {previewItems.map((item, index) => (
                  <PreviewCard
                    key={`${item.label}-${index}`}
                    item={item}
                    onToggle={(accepted) =>
                      setPreviewItems((current) =>
                        current.map((candidate, candidateIndex) =>
                          candidateIndex === index
                            ? { ...candidate, accepted }
                            : candidate,
                        ),
                      )
                    }
                  />
                ))}
              </div>
              <div className="capture-footer">
                <button
                  type="button"
                  className="button-primary"
                  onClick={handleSave}
                  disabled={isSaving || !acceptedItems.length}
                >
                  Save accepted
                </button>
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
