import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Message from '../components/query/Message.jsx'
import StreamingMessage from '../components/query/StreamingMessage.jsx'
import SessionCard from '../components/history/SessionCard.jsx'
import PrivacyStatus from '../components/privacy/PrivacyStatus.jsx'

function createPrivacy(overrides = {}) {
  return {
    execution_mode: 'local',
    routing_enforced: true,
    warning_present: false,
    provider_label: 'Local model',
    model_label: 'llama3.1:8b',
    summary: 'Processed locally with privacy rules applied.',
    ...overrides,
  }
}

describe('PrivacyStatus', () => {
  it('renders nothing when privacy metadata is missing', () => {
    const { container } = render(<PrivacyStatus privacy={null} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('renders a blocked badge and summary for protected requests', () => {
    render(
      <PrivacyStatus
        privacy={createPrivacy({
          execution_mode: 'blocked',
          summary: 'Blocked to protect local-only data from being sent externally.',
        })}
      />,
    )

    expect(screen.getByText('Blocked')).toBeInTheDocument()
    expect(screen.getByText('Privacy rules applied')).toBeInTheDocument()
    expect(
      screen.getByText('Blocked to protect local-only data from being sent externally.'),
    ).toBeInTheDocument()
  })

  it('supports compact hidden-summary and unknown privacy states', () => {
    render(
      <PrivacyStatus
        privacy={createPrivacy({
          execution_mode: 'mystery',
          summary: 'This should stay hidden.',
        })}
        compact
        showSummary={false}
      />,
    )

    expect(screen.getByText('Unknown')).toBeInTheDocument()
    expect(document.querySelector('.privacy-status')).toHaveClass('compact')
    expect(screen.queryByText('This should stay hidden.')).not.toBeInTheDocument()
  })
})

describe('Message', () => {
  it('renders user messages without assistant metadata', () => {
    render(<Message message={{ role: 'user', content: 'Keep this local.' }} />)

    expect(screen.getByText('Keep this local.')).toBeInTheDocument()
    expect(screen.queryByText('Privacy rules applied')).not.toBeInTheDocument()
  })

  it('renders markdown in assistant messages', () => {
    render(
      <Message
        message={{
          role: 'assistant',
          content: '- grounded\n- careful',
        }}
      />,
    )

    expect(screen.getByText('grounded')).toBeInTheDocument()
    expect(screen.getByText('careful')).toBeInTheDocument()
  })

  it('shows privacy state and query metadata for assistant responses', () => {
    render(
      <Message
        message={{
          role: 'assistant',
          content: 'You care about long-term work and honest relationships.',
          metadata: {
            backend_used: 'external',
            attributes_used: 4,
            domains_referenced: ['goals', 'values'],
            duration_ms: 1250,
            privacy: createPrivacy({
              execution_mode: 'external',
              provider_label: 'Anthropic',
              model_label: 'claude-sonnet-4-6',
              summary: 'Used an external model after privacy rules were applied.',
            }),
          },
        }}
      />,
    )

    expect(screen.getByText('External')).toBeInTheDocument()
    expect(
      screen.getByText('Used an external model after privacy rules were applied.'),
    ).toBeInTheDocument()
    expect(screen.getByText('4 attributes')).toBeInTheDocument()
    expect(screen.getByText('goals, values')).toBeInTheDocument()
    expect(screen.getByText('1.3s')).toBeInTheDocument()
  })
})

describe('StreamingMessage', () => {
  it('preserves blocked privacy guidance alongside stream errors', () => {
    render(
      <StreamingMessage
        message={{
          content: '',
          error: 'This request was blocked to protect local-only data from being sent to an external model.',
          privacy: createPrivacy({
            execution_mode: 'blocked',
            summary: 'Blocked to protect local-only data from being sent externally.',
          }),
        }}
      />,
    )

    expect(screen.getByText('Blocked')).toBeInTheDocument()
    expect(
      screen.getByText('Blocked to protect local-only data from being sent externally.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'This request was blocked to protect local-only data from being sent to an external model.',
      ),
    ).toBeInTheDocument()
  })
})

describe('SessionCard', () => {
  it('shows compact session privacy state and blocked entry details when expanded', async () => {
    const user = userEvent.setup()

    render(
      <SessionCard
        session={{
          id: 'session-1',
          session_type: 'freeform',
          summary: '2 queries across session',
          attributes_created: 0,
          attributes_updated: 0,
          external_calls_made: 1,
          started_at: '2026-04-16T12:00:00Z',
          ended_at: '2026-04-16T12:05:00Z',
          privacy: createPrivacy({
            execution_mode: 'blocked',
            summary:
              'This session included a blocked external attempt to protect local-only data.',
          }),
          routing_log: [
            {
              query_type: 'open_ended',
              backend: 'external',
              attribute_count: 4,
              timestamp: '2026-04-16T12:01:00Z',
              domains_referenced: ['goals', 'values'],
              privacy: createPrivacy({
                execution_mode: 'external',
                summary: 'Used an external model after privacy rules were applied.',
              }),
            },
            {
              query_type: 'open_ended',
              backend: 'external',
              attribute_count: 3,
              timestamp: '2026-04-16T12:03:00Z',
              domains_referenced: ['fears'],
              privacy: createPrivacy({
                execution_mode: 'blocked',
                summary:
                  'Blocked to protect local-only data from being sent externally.',
              }),
            },
          ],
        }}
      />,
    )

    expect(screen.getByText('Freeform session')).toBeInTheDocument()
    expect(
      screen.getByText(
        'This session included a blocked external attempt to protect local-only data.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText('Domains: fears')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button'))

    expect(screen.getByText('Domains: fears')).toBeInTheDocument()
    expect(
      screen.getByText('Blocked to protect local-only data from being sent externally.'),
    ).toBeInTheDocument()
  })

  it('supports keyboard expansion and empty routing logs', async () => {
    const user = userEvent.setup()

    render(
      <SessionCard
        session={{
          id: 'session-2',
          session_type: 'vault',
          summary: 'Imported notes',
          attributes_created: 1,
          attributes_updated: 0,
          external_calls_made: 0,
          started_at: '2026-04-16T12:00:00Z',
          ended_at: '2026-04-16T12:05:00Z',
          privacy: createPrivacy({
            execution_mode: 'local',
          }),
          routing_log: [],
        }}
      />,
    )

    const card = screen.getByRole('button')
    card.focus()
    await user.keyboard('{Enter}')

    expect(screen.getByText('Vault import')).toBeInTheDocument()
    expect(screen.getByText('No routing log entries recorded for this session.')).toBeInTheDocument()
  })
})
