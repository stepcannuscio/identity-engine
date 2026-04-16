import { useEffect, useRef } from 'react'
import Message from './Message.jsx'
import StreamingMessage from './StreamingMessage.jsx'

export default function MessageList({
  messages,
  streamingMessage,
  toasts,
  onDismissToast,
}) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, streamingMessage, toasts])

  return (
    <div className="message-list">
      <div className="message-stack">
        {toasts.length > 0 ? (
          <div className="toast-stack" aria-live="polite">
            {toasts.map((toast) => (
              <button
                key={toast.id}
                type="button"
                className="toast"
                onClick={() => onDismissToast(toast.id)}
              >
                {toast.message}
              </button>
            ))}
          </div>
        ) : null}

        {messages.length === 0 && !streamingMessage ? (
          <div className="empty-prompt">
            What would you like to know about yourself?
          </div>
        ) : null}

        {messages.map((message, index) => (
          <Message
            key={`${message.role}-${index}-${message.content.slice(0, 20)}`}
            message={message}
          />
        ))}

        {streamingMessage ? <StreamingMessage message={streamingMessage} /> : null}
        <div ref={endRef} />
      </div>
    </div>
  )
}
