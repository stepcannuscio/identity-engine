import { useEffect, useRef } from 'react'
import { ArrowUp, Square, X } from 'lucide-react'

export default function QueryInput({
  value,
  onChange,
  onSend,
  onAbort,
  isStreaming,
}) {
  const textareaRef = useRef(null)

  useEffect(() => {
    if (!textareaRef.current) {
      return
    }

    textareaRef.current.style.height = '0px'
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`
  }, [value])

  const handleKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (isStreaming) {
        return
      }
      onSend(value)
    }
  }

  return (
    <div className="query-input-bar">
      <form
        className="query-form"
        onSubmit={(event) => {
          event.preventDefault()
          if (isStreaming) {
            return
          }
          onSend(value)
        }}
      >
        <div className="query-input-wrap">
          <textarea
            ref={textareaRef}
            className="query-textarea"
            placeholder="Ask anything about yourself..."
            value={value}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={handleKeyDown}
            readOnly={isStreaming}
            rows={1}
          />
          {value ? (
            <button
              type="button"
              className="icon-button"
              onClick={() => onChange('')}
              aria-label="Clear input"
            >
              <X size={16} />
            </button>
          ) : null}
          <button
            type={isStreaming ? 'button' : 'submit'}
            className="icon-button primary"
            onClick={isStreaming ? onAbort : undefined}
            disabled={!isStreaming && !value.trim()}
            aria-label={isStreaming ? 'Stop response' : 'Send query'}
          >
            {isStreaming ? <Square size={16} /> : <ArrowUp size={16} />}
          </button>
        </div>
      </form>
    </div>
  )
}
