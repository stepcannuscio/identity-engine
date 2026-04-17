import { act, renderHook, waitFor } from '@testing-library/react'
import { useStream } from '../hooks/useStream.js'
import { useAppState } from '../store/appState.js'
import { createPrivacy } from './fixtures.js'
import { createWrapper } from './renderWithProviders.jsx'
import {
  createJsonErrorResponse,
  createStreamResponse,
} from './stream.js'

function renderUseStream(options = {}) {
  return renderHook(
    () => ({
      stream: useStream(),
      state: useAppState(),
    }),
    {
      wrapper: createWrapper({
        appState: {
          token: 'session-token',
          ...options.appState,
        },
      }),
    },
  )
}

describe('useStream', () => {
  it('streams tokens, metadata, and forwards the auth header', async () => {
    const onToken = vi.fn()
    const onMetadata = vi.fn()
    const onComplete = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(
      createStreamResponse(
        [
          { type: 'token', content: 'Hello' },
          { type: 'token', content: ' world' },
          {
            type: 'metadata',
            content: {
              backend_used: 'external',
              attributes_used: 2,
              domains_referenced: ['goals'],
              duration_ms: 820,
              privacy: createPrivacy({ execution_mode: 'external' }),
            },
          },
        ],
        { split: true },
      ),
    )

    const { result } = renderUseStream()

    await act(async () => {
      await result.current.stream.streamQuery({
        query: 'What do I want next?',
        backend: 'external',
        onToken,
        onMetadata,
        onError: vi.fn(),
        onComplete,
        onAbort: vi.fn(),
      })
    })

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/query/stream',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer session-token',
          'Content-Type': 'application/json',
        }),
      }),
    )
    expect(onToken).toHaveBeenNthCalledWith(1, 'Hello')
    expect(onToken).toHaveBeenNthCalledWith(2, ' world')
    expect(onMetadata).toHaveBeenCalledWith(
      expect.objectContaining({
        backend_used: 'external',
      }),
    )
    expect(onComplete).toHaveBeenCalledWith(
      expect.objectContaining({
        hadError: false,
        metadata: expect.objectContaining({
          backend_used: 'external',
        }),
      }),
    )
  })

  it('normalizes JSON error responses and preserves privacy metadata', async () => {
    const onError = vi.fn()
    const onComplete = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(
      createJsonErrorResponse(403, {
        message: 'Blocked to protect local-only data.',
        privacy: createPrivacy({
          execution_mode: 'blocked',
          summary: 'Blocked to protect local-only data.',
        }),
      }),
    )

    const { result } = renderUseStream()

    await act(async () => {
      await result.current.stream.streamQuery({
        query: 'Tell me about my fears',
        backend: 'external',
        onToken: vi.fn(),
        onMetadata: vi.fn(),
        onError,
        onComplete,
        onAbort: vi.fn(),
      })
    })

    expect(onError).toHaveBeenCalledWith({
      message: 'Blocked to protect local-only data.',
      privacy: expect.objectContaining({
        execution_mode: 'blocked',
      }),
    })
    expect(onComplete).toHaveBeenCalledWith({ metadata: null, hadError: true })
  })

  it('falls back to the response status text when the error payload is not JSON', async () => {
    const onError = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(
      new Response('server exploded', {
        status: 500,
        statusText: 'Server exploded',
      }),
    )

    const { result } = renderUseStream()

    await act(async () => {
      await result.current.stream.streamQuery({
        query: 'Why did this fail?',
        backend: 'local',
        onToken: vi.fn(),
        onMetadata: vi.fn(),
        onError,
        onComplete: vi.fn(),
        onAbort: vi.fn(),
      })
    })

    expect(onError).toHaveBeenCalledWith({
      message: 'Server exploded',
      privacy: null,
    })
  })

  it('adds a warning toast when the stream reports sensitive external processing', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      createStreamResponse([
        { type: 'warning', content: 'warning' },
        { type: 'token', content: 'Hi' },
      ]),
    )

    const { result } = renderUseStream()

    await act(async () => {
      await result.current.stream.streamQuery({
        query: 'Sensitive question',
        backend: 'external',
        onToken: vi.fn(),
        onMetadata: vi.fn(),
        onError: vi.fn(),
        onComplete: vi.fn(),
        onAbort: vi.fn(),
      })
    })

    await waitFor(() => {
      expect(result.current.state.toasts).toHaveLength(1)
    })

    expect(result.current.state.toasts[0].message).toContain(
      'Sensitive content detected',
    )
  })

  it('calls onAbort without onComplete when the stream is cancelled', async () => {
    const encoder = new TextEncoder()
    const onAbort = vi.fn()
    const onComplete = vi.fn()

    global.fetch = vi.fn().mockImplementation((_, options) => {
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify({ type: 'token', content: 'Partial' })}\n\n`),
          )
          options.signal.addEventListener('abort', () => {
            controller.error(new DOMException('Aborted', 'AbortError'))
          })
        },
      })

      return Promise.resolve(
        new Response(stream, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
      )
    })

    const { result } = renderUseStream()

    let streamPromise
    await act(async () => {
      streamPromise = result.current.stream.streamQuery({
        query: 'Abort me',
        backend: 'local',
        onToken: vi.fn(),
        onMetadata: vi.fn(),
        onError: vi.fn(),
        onComplete,
        onAbort,
      })
      await Promise.resolve()
    })

    await act(async () => {
      result.current.stream.abortStream()
      await streamPromise
    })

    expect(onAbort).toHaveBeenCalledTimes(1)
    expect(onComplete).not.toHaveBeenCalled()
  })
})
