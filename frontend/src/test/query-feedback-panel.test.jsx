import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/endpoints.js', () => ({
  submitQueryFeedback: vi.fn(),
}))

import QueryFeedbackPanel from '../components/query/QueryFeedbackPanel.jsx'
import { submitQueryFeedback } from '../api/endpoints.js'
import { renderWithProviders } from './renderWithProviders.jsx'

describe('QueryFeedbackPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('submits voice-specific feedback for voice generation messages', async () => {
    const user = userEvent.setup()
    submitQueryFeedback.mockResolvedValue({ id: 'feedback-1', stored: true })

    renderWithProviders(
      <QueryFeedbackPanel
        message={{
          query: 'Rewrite this email so it sounds like me.',
          content: 'Thanks for the note. I want to keep this direct and calm.',
          metadata: {
            query_type: 'simple',
            backend_used: 'local',
            confidence: 'medium_confidence',
            intent: {
              source_profile: 'voice_generation',
              intent_tags: ['voice_adaptation', 'writing_task'],
              domain_hints: ['voice'],
            },
            domains_referenced: ['voice'],
            retrieved_attribute_ids: ['attr-1'],
          },
        }}
      />,
    )

    expect(screen.getByText('Did this sound like you?')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Too formal' }))
    await user.click(screen.getByRole('button', { name: 'Save feedback' }))

    expect(submitQueryFeedback).toHaveBeenCalledWith(
      expect.objectContaining({
        feedback: 'wrong_focus',
        voice_feedback: 'too_formal',
        retrieved_attribute_ids: ['attr-1'],
      }),
    )
  })
})
