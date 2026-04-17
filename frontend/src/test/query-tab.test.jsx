import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import QueryTab from '../components/query/QueryTab.jsx'
import { createPrivacy } from './fixtures.js'
import { renderWithProviders } from './renderWithProviders.jsx'
import { createStreamResponse } from './stream.js'

describe('QueryTab', () => {
  it('sends a query, appends the assistant response, and dismisses warning toasts', async () => {
    const user = userEvent.setup()
    global.fetch = vi.fn().mockResolvedValue(
      createStreamResponse([
        { type: 'warning', content: 'warning' },
        { type: 'token', content: 'You' },
        { type: 'token', content: ' value calm focus.' },
        {
          type: 'metadata',
          content: {
            backend_used: 'external',
            attributes_used: 3,
            domains_referenced: ['values'],
            duration_ms: 910,
            privacy: createPrivacy({
              execution_mode: 'external',
              summary: 'Used an external model after privacy rules were applied.',
            }),
          },
        },
      ]),
    )

    renderWithProviders(<QueryTab />, {
      appState: {
        token: 'session-token',
        backend: 'external',
      },
    })

    await user.type(
      screen.getByPlaceholderText('Ask anything about yourself...'),
      'What do I value?',
    )
    await user.click(screen.getByRole('button', { name: 'Send query' }))

    expect(await screen.findByText('What do I value?')).toBeInTheDocument()
    expect(
      await screen.findByText('Sensitive content detected - processed externally per your selection'),
    ).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('You value calm focus.')).toBeInTheDocument()
    })

    await user.click(
      screen.getByRole('button', {
        name: 'Sensitive content detected - processed externally per your selection',
      }),
    )

    await waitFor(() => {
      expect(
        screen.queryByText(
          'Sensitive content detected - processed externally per your selection',
        ),
      ).not.toBeInTheDocument()
    })

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/query/stream',
      expect.objectContaining({
        body: JSON.stringify({
          query: 'What do I value?',
          backend_override: 'external',
        }),
      }),
    )
  })

  it('retains blocked errors with privacy context instead of committing an assistant message', async () => {
    const user = userEvent.setup()
    global.fetch = vi.fn().mockResolvedValue(
      createStreamResponse([
        {
          type: 'error',
          content: 'Blocked to protect local-only data from being sent externally.',
          privacy: createPrivacy({
            execution_mode: 'blocked',
            summary: 'Blocked to protect local-only data from being sent externally.',
          }),
        },
      ]),
    )

    renderWithProviders(<QueryTab />, {
      appState: {
        token: 'session-token',
        backend: 'external',
      },
    })

    await user.type(
      screen.getByPlaceholderText('Ask anything about yourself...'),
      'Tell me about my fears',
    )
    await user.click(screen.getByRole('button', { name: 'Send query' }))

    expect(await screen.findByText('Tell me about my fears')).toBeInTheDocument()
    expect(
      await screen.findAllByText(
        'Blocked to protect local-only data from being sent externally.',
      ),
    ).toHaveLength(2)
    expect(screen.getByText('Blocked')).toBeInTheDocument()
    expect(document.querySelector('.message.assistant')).toBeNull()
  })

  it('renders acquisition suggestions from streamed query metadata', async () => {
    const user = userEvent.setup()
    global.fetch = vi.fn().mockResolvedValue(
      createStreamResponse([
        { type: 'token', content: 'I do not have enough context yet.' },
        {
          type: 'metadata',
          content: {
            backend_used: 'local',
            attributes_used: 0,
            domains_referenced: [],
            duration_ms: 120,
            privacy: createPrivacy(),
            acquisition: {
              status: 'suggested',
              gaps: [{ kind: 'identity', domain: 'goals', reason: 'thin coverage' }],
              suggestions: [
                {
                  kind: 'quick_capture',
                  prompt: "I don't know much about your goals yet.",
                  action: {
                    target: 'attribute',
                    domain_hint: 'goals',
                    placeholder: 'Share a quick goal note.',
                  },
                },
              ],
            },
          },
        },
      ]),
    )

    renderWithProviders(<QueryTab />, {
      appState: {
        token: 'session-token',
        backend: 'local',
      },
    })

    await user.type(
      screen.getByPlaceholderText('Ask anything about yourself...'),
      'What are my current goals?',
    )
    await user.click(screen.getByRole('button', { name: 'Send query' }))

    expect(await screen.findByText("I don't know much about your goals yet.")).toBeInTheDocument()
    expect(screen.getByText('Next best input')).toBeInTheDocument()
  })
})
