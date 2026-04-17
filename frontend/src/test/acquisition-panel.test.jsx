import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Message from '../components/query/Message.jsx'
import {
  capture,
  capturePreview,
  createPreferenceSignal,
  previewInterview,
  saveInterview,
  uploadArtifact,
} from '../api/endpoints.js'
import { createPrivacy } from './fixtures.js'
import { createTestQueryClient, renderWithProviders } from './renderWithProviders.jsx'

vi.mock('../api/endpoints.js', () => ({
  capture: vi.fn(),
  capturePreview: vi.fn(),
  createPreferenceSignal: vi.fn(),
  previewInterview: vi.fn(),
  saveInterview: vi.fn(),
  uploadArtifact: vi.fn(),
}))

function renderAcquisitionMessage(suggestion) {
  const queryClient = createTestQueryClient()
  const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries')
  renderWithProviders(
    <Message
      message={{
        role: 'assistant',
        content: 'I only have partial context right now.',
        metadata: {
          privacy: createPrivacy(),
          acquisition: {
            status: 'suggested',
            gaps: [],
            suggestions: [suggestion],
          },
        },
      }}
    />,
    { queryClient },
  )
  return { invalidateQueries }
}

describe('AcquisitionPanel', () => {
  it('submits attribute quick capture suggestions through preview and save', async () => {
    const user = userEvent.setup()
    capturePreview.mockResolvedValue({
      proposed: [
        {
          domain: 'goals',
          label: 'priority',
          value: 'Ship the backend cleanly.',
          elaboration: null,
          mutability: 'evolving',
          confidence: 0.7,
        },
      ],
    })
    capture.mockResolvedValue({ attributes_saved: 1 })

    const { invalidateQueries } = renderAcquisitionMessage({
      kind: 'quick_capture',
      prompt: "I don't know much about your goals yet.",
      action: {
        target: 'attribute',
        domain_hint: 'goals',
        placeholder: 'Share a quick goal note.',
      },
    })

    await user.click(screen.getByRole('button', { name: 'Open capture' }))
    await user.type(screen.getByPlaceholderText('Share a quick goal note.'), 'Ship the backend cleanly this quarter.')
    await user.click(screen.getByRole('button', { name: 'Save quick note' }))

    await waitFor(() => {
      expect(capturePreview).toHaveBeenCalledWith(
        'Ship the backend cleanly this quarter.',
        'goals',
      )
    })
    expect(capture).toHaveBeenCalledWith(
      'Ship the backend cleanly this quarter.',
      'goals',
      [
        {
          domain: 'goals',
          label: 'priority',
          value: 'Ship the backend cleanly.',
          elaboration: null,
          mutability: 'evolving',
          confidence: 0.7,
        },
      ],
    )
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['attributes'] })
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['domains'] })
  })

  it('submits preference quick capture suggestions as preference signals', async () => {
    const user = userEvent.setup()
    createPreferenceSignal.mockResolvedValue({ id: 'signal-1' })

    renderAcquisitionMessage({
      kind: 'quick_capture',
      prompt: "I don't have enough preference signals yet.",
      action: {
        target: 'preference_signal',
        category: 'planning',
        subject: 'weeknight_meals',
        signal: 'prefer',
        strength: 3,
        placeholder: 'Example: prefer quick weeknight meals',
      },
    })

    await user.click(screen.getByRole('button', { name: 'Add preference' }))
    await user.click(screen.getByRole('button', { name: 'Save preference' }))

    await waitFor(() => {
      expect(createPreferenceSignal).toHaveBeenCalledWith({
        category: 'planning',
        subject: 'weeknight_meals',
        signal: 'prefer',
        strength: 3,
        source: 'explicit_feedback',
      })
    })
  })

  it('submits interview suggestions through preview and save', async () => {
    const user = userEvent.setup()
    previewInterview.mockResolvedValue({
      proposed: [
        {
          domain: 'values',
          label: 'non_negotiable',
          value: 'Honesty is non-negotiable for me.',
          elaboration: null,
          mutability: 'stable',
          confidence: 0.8,
        },
      ],
    })
    saveInterview.mockResolvedValue({ attributes_saved: 1 })

    renderAcquisitionMessage({
      kind: 'interview_question',
      prompt: 'What are the two or three things you would never compromise on?',
      action: {
        domain: 'values',
        question: 'What are the two or three things you would never compromise on?',
        placeholder: 'Answer in your own words.',
      },
    })

    await user.click(screen.getByRole('button', { name: 'Answer question' }))
    await user.type(screen.getByPlaceholderText('Answer in your own words.'), 'Honesty and privacy.')
    await user.click(screen.getByRole('button', { name: 'Save answer' }))

    await waitFor(() => {
      expect(previewInterview).toHaveBeenCalledWith(
        'values',
        'What are the two or three things you would never compromise on?',
        'Honesty and privacy.',
      )
    })
    expect(saveInterview).toHaveBeenCalledWith(
      'values',
      'What are the two or three things you would never compromise on?',
      'Honesty and privacy.',
      [
        {
          domain: 'values',
          label: 'non_negotiable',
          value: 'Honesty is non-negotiable for me.',
          elaboration: null,
          mutability: 'stable',
          confidence: 0.8,
        },
      ],
    )
  })

  it('submits artifact upload suggestions as local artifacts', async () => {
    const user = userEvent.setup()
    uploadArtifact.mockResolvedValue({ artifact_id: 'artifact-1', chunk_count: 1 })

    renderAcquisitionMessage({
      kind: 'artifact_upload',
      prompt: 'Upload a note for better grounding.',
      action: {
        domain: 'patterns',
        title: 'Notes about burnout',
        type: 'note',
        source: 'upload',
        placeholder: 'Paste a note here or choose a text file upload.',
      },
    })

    await user.click(screen.getByRole('button', { name: 'Upload note' }))
    await user.type(
      screen.getByPlaceholderText('Paste a note here or choose a text file upload.'),
      'Burnout shows up when meetings stack all afternoon.',
    )
    await user.click(screen.getByRole('button', { name: 'Save artifact' }))

    await waitFor(() => {
      expect(uploadArtifact).toHaveBeenCalledWith({
        text: 'Burnout shows up when meetings stack all afternoon.',
        file: null,
        title: 'Notes about burnout',
        type: 'note',
        source: 'upload',
        domain: 'patterns',
      })
    })
  })
})
