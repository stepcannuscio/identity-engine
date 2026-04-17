import { useCallback, useEffect, useRef } from 'react'
import { API_BASE_URL } from '../api/client.js'
import { useAppState } from '../store/appState.js'

function extractEventPayload(block) {
  const payload = block
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .join('\n')

  if (!payload) {
    return null
  }

  return JSON.parse(payload)
}

function normalizeStreamError(message, privacy = null) {
  return {
    message: message || 'Unable to stream response.',
    privacy,
  }
}

export function useStream() {
  const controllerRef = useRef(null)
  const { token, addToast } = useAppState()

  const abortStream = useCallback(() => {
    controllerRef.current?.abort()
  }, [])

  useEffect(() => () => controllerRef.current?.abort(), [])

  const streamQuery = useCallback(
    async ({
      query,
      backend,
      onToken,
      onMetadata,
      onError,
      onComplete,
      onAbort,
    }) => {
      controllerRef.current?.abort()
      const controller = new AbortController()
      controllerRef.current = controller

      let metadata = null
      let hadError = false
      let aborted = false

      try {
        const response = await fetch(`${API_BASE_URL}/query/stream`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            query,
            backend_override: backend,
          }),
          signal: controller.signal,
        })

        if (!response.ok || !response.body) {
          let errorPayload = normalizeStreamError('Unable to stream response.')
          hadError = true

          try {
            const payload = await response.json()
            errorPayload = normalizeStreamError(
              payload.message || payload.detail || payload.error,
              payload.privacy ?? null,
            )
          } catch {
            errorPayload = normalizeStreamError(response.statusText)
          }

          onError?.(errorPayload)
          return
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        const handleBlock = (block) => {
          if (!block.trim()) {
            return
          }

          const event = extractEventPayload(block)
          if (!event) {
            return
          }

          switch (event.type) {
            case 'token':
              onToken?.(event.content ?? '')
              break
            case 'metadata':
              metadata = event.content ?? null
              onMetadata?.(metadata)
              break
            case 'warning':
              addToast({
                message:
                  'Sensitive content detected - processed externally per your selection',
                tone: 'warning',
                duration: 5000,
              })
              break
            case 'error':
              hadError = true
              onError?.(
                normalizeStreamError(
                  event.content || 'Unable to stream response.',
                  event.privacy ?? null,
                ),
              )
              break
            default:
              break
          }
        }

        while (true) {
          const { value, done } = await reader.read()
          if (done) {
            break
          }

          buffer += decoder.decode(value, { stream: true })
          let boundary = buffer.indexOf('\n\n')

          while (boundary >= 0) {
            const block = buffer.slice(0, boundary)
            buffer = buffer.slice(boundary + 2)
            handleBlock(block)
            boundary = buffer.indexOf('\n\n')
          }
        }

        if (buffer.trim()) {
          handleBlock(buffer)
        }
      } catch (error) {
        if (error.name === 'AbortError') {
          aborted = true
          onAbort?.()
          return
        }

        hadError = true
        onError?.(normalizeStreamError(error.message))
      } finally {
        if (controllerRef.current === controller) {
          controllerRef.current = null
        }

        if (!aborted) {
          onComplete?.({ metadata, hadError })
        }
      }
    },
    [addToast, token],
  )

  return { streamQuery, abortStream }
}
