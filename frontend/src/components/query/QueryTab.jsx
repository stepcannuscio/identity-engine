import { useCallback, useRef, useState } from 'react'
import MessageList from './MessageList.jsx'
import QueryInput from './QueryInput.jsx'
import { useStream } from '../../hooks/useStream.js'
import { useAppState } from '../../store/appState.js'

export default function QueryTab() {
  const {
    messages,
    addMessage,
    isStreaming,
    setStreaming,
    backend,
    toasts,
    removeToast,
  } = useAppState()
  const [draft, setDraft] = useState('')
  const [streamingMessage, setStreamingMessage] = useState(null)
  const streamingRef = useRef(null)
  const { streamQuery, abortStream } = useStream()

  const updateStreamingMessage = useCallback((nextValue) => {
    const value =
      typeof nextValue === 'function' ? nextValue(streamingRef.current) : nextValue
    streamingRef.current = value
    setStreamingMessage(value)
  }, [])

  const handleSend = useCallback(
    async (submittedText) => {
      const text = (submittedText ?? draft).trim()
      if (!text || isStreaming) {
        return
      }

      addMessage({ role: 'user', content: text })
      setDraft('')
      updateStreamingMessage({ content: '', metadata: null, error: null, privacy: null })
      setStreaming(true)

      await streamQuery({
        query: text,
        backend,
        onToken: (token) => {
          updateStreamingMessage((current) => ({
            ...(current ?? { content: '', metadata: null, error: null }),
            content: `${current?.content ?? ''}${token}`,
          }))
        },
        onMetadata: (metadata) => {
          updateStreamingMessage((current) => ({
            ...(current ?? { content: '', error: null, privacy: null }),
            metadata,
          }))
        },
        onError: (error) => {
          updateStreamingMessage((current) => ({
            ...(current ?? { content: '', metadata: null, privacy: null }),
            error: error.message,
            privacy: error.privacy ?? current?.metadata?.privacy ?? current?.privacy ?? null,
          }))
        },
        onAbort: () => {
          const current = streamingRef.current
          if (current?.content?.trim()) {
            addMessage({
              role: 'assistant',
              content: current.content,
              metadata: current.metadata,
            })
          }
          setStreaming(false)
          updateStreamingMessage(null)
        },
        onComplete: ({ metadata, hadError }) => {
          const current = streamingRef.current
          const finalMessage = current
            ? {
                ...current,
                metadata: metadata ?? current.metadata,
              }
            : null

          setStreaming(false)

          if (!finalMessage) {
            updateStreamingMessage(null)
            return
          }

          if (!hadError && !finalMessage.error && finalMessage.content.trim()) {
            addMessage({
              role: 'assistant',
              content: finalMessage.content,
              metadata: finalMessage.metadata,
            })
            updateStreamingMessage(null)
            return
          }

          updateStreamingMessage(finalMessage)
        },
      })
    },
    [addMessage, backend, draft, isStreaming, setStreaming, streamQuery, updateStreamingMessage],
  )

  return (
    <section className="query-tab">
      <MessageList
        messages={messages}
        streamingMessage={streamingMessage}
        toasts={toasts}
        onDismissToast={removeToast}
      />
      <QueryInput
        value={draft}
        onChange={setDraft}
        onSend={handleSend}
        onAbort={abortStream}
        isStreaming={isStreaming}
      />
    </section>
  )
}
