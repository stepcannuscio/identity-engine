import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useQuery } from '@tanstack/react-query'
import { vi } from 'vitest'
import CapturePanel from '../components/graph/CapturePanel.jsx'
import TeachTab from '../components/teach/TeachTab.jsx'
import { renderWithProviders } from './renderWithProviders.jsx'
import {
  answerTeachQuestion,
  capture,
  capturePreview,
  getTeachBootstrap,
} from '../api/endpoints.js'

vi.mock('../api/endpoints.js', () => ({
  analyzeArtifact: vi.fn(),
  answerTeachQuestion: vi.fn(),
  capture: vi.fn(),
  capturePreview: vi.fn(),
  feedbackTeachQuestion: vi.fn(),
  getArtifactAnalysis: vi.fn(),
  getTeachBootstrap: vi.fn(),
  promoteArtifact: vi.fn(),
  saveProviderCredentials: vi.fn(),
  saveSetupProfile: vi.fn(),
  updateSecurityCheckOverride: vi.fn(),
  uploadArtifact: vi.fn(),
}))

function createBootstrapData(overrides = {}) {
  return {
    cards: [
      {
        title: 'Teach the engine',
        body: 'Share helpful information at your own pace.',
      },
    ],
    privacy_preferences: [
      {
        code: 'balanced',
        label: 'Balanced',
        description: 'Balance privacy and capability.',
      },
    ],
    profiles: [
      {
        code: 'balanced_hybrid',
        label: 'Balanced hybrid',
        description: 'Use a mix of local and external support.',
        recommendation_reason: 'Good default for mixed setups.',
        provider_scope: 'mixed',
        default_backend: 'external',
        provider_options: ['anthropic'],
        recommended_provider: 'anthropic',
        available: true,
        requires_external_provider: true,
      },
    ],
    providers: [
      {
        provider: 'anthropic',
        label: 'Anthropic',
        deployment: 'external',
        trust_boundary: 'third_party_external',
        available: true,
        auth_strategy: 'api_key',
        credential_fields: [{ name: 'api_key', label: 'API key', secret: true }],
      },
    ],
    security_posture: {
      supported: true,
      platform: 'macos',
      checks: [],
    },
    questions: [
      {
        id: 'question-1',
        prompt: 'What matters most to you right now?',
        domain: 'values',
        source: 'seed',
        intent_key: 'values_what_matters_most_to_you_right_now',
        status: 'pending',
        priority: 10,
      },
    ],
    ...overrides,
  }
}

function createBootstrapQuery(overrides = {}) {
  return {
    isLoading: false,
    data: createBootstrapData(overrides),
  }
}

function TeachTabHarness() {
  const bootstrapQuery = useQuery({
    queryKey: ['teachBootstrap'],
    queryFn: getTeachBootstrap,
    retry: false,
  })

  return <TeachTab bootstrapQuery={bootstrapQuery} />
}

describe('external extraction consent UI', () => {
  it('advances to the returned next question after saving an answer', async () => {
    const user = userEvent.setup()
    getTeachBootstrap.mockResolvedValueOnce(createBootstrapData())
    answerTeachQuestion.mockResolvedValue({
      next: createBootstrapData({
        questions: [
          {
            id: 'question-2',
            prompt: 'What tone do you default to when you are most yourself?',
            domain: 'voice',
            source: 'catalog',
            intent_key: 'voice_what_tone_do_you_default_to_when_you_are_most_yourself',
            status: 'pending',
            priority: 10,
          },
        ],
      }),
    })

    renderWithProviders(<TeachTabHarness />, {
      appState: { backend: 'local' },
    })

    expect(await screen.findByText('What matters most to you right now?')).toBeInTheDocument()

    await user.type(
      screen.getByPlaceholderText(/answer in your own words\./i),
      'Honest relationships matter most to me.',
    )
    await user.click(screen.getByRole('button', { name: 'Save answer' }))

    await waitFor(() => {
      expect(answerTeachQuestion).toHaveBeenCalledWith('question-1', {
        answer: 'Honest relationships matter most to me.',
      })
    })

    expect(
      await screen.findByText('What tone do you default to when you are most yourself?'),
    ).toBeInTheDocument()
    expect(screen.queryByText('What matters most to you right now?')).not.toBeInTheDocument()
  })

  it('requires explicit consent before external quick-capture preview', async () => {
    const user = userEvent.setup()
    capturePreview.mockResolvedValue({
      proposed: [
        {
          domain: 'patterns',
          label: 'morning_focus',
          value: 'I focus best in the morning.',
          elaboration: null,
          mutability: 'evolving',
          confidence: 0.7,
          conflicts_with: null,
        },
      ],
    })

    renderWithProviders(
      <CapturePanel domains={[{ domain: 'patterns' }]} onSaved={vi.fn()} />,
      { appState: { backend: 'external' } },
    )

    await user.click(screen.getByRole('button', { name: /\+ quick capture/i }))
    await user.type(screen.getByLabelText(/what's on your mind\?/i), 'I focus best in the morning.')

    const previewButton = screen.getByRole('button', { name: 'Preview' })
    expect(previewButton).toBeDisabled()

    await user.click(
      screen.getByLabelText(
        /i understand this raw note may be sent to my configured external provider for extraction\./i,
      ),
    )

    expect(previewButton).toBeEnabled()
    await user.click(previewButton)

    await waitFor(() => {
      expect(capturePreview).toHaveBeenCalledWith(
        'I focus best in the morning.',
        '',
        true,
      )
    })
  })

  it('requires consent before external teach answer extraction and forwards the flag', async () => {
    const user = userEvent.setup()
    answerTeachQuestion.mockResolvedValue({})

    renderWithProviders(<TeachTab bootstrapQuery={createBootstrapQuery()} />, {
      appState: { backend: 'external' },
    })

    await user.type(
      screen.getByPlaceholderText(/answer in your own words\./i),
      'Honest relationships matter most to me.',
    )

    const saveAnswerButton = screen.getByRole('button', { name: 'Save answer' })
    expect(saveAnswerButton).toBeDisabled()

    await user.click(
      screen.getByLabelText(
        /i understand this raw answer may be sent to my configured external provider for extraction\./i,
      ),
    )

    expect(saveAnswerButton).toBeEnabled()
    await user.click(saveAnswerButton)

    await waitFor(() => {
      expect(answerTeachQuestion).toHaveBeenCalledWith('question-1', {
        answer: 'Honest relationships matter most to me.',
        allow_external_extraction: true,
      })
    })
  })

  it('requires consent before external teach quick-note extraction and forwards the flag', async () => {
    const user = userEvent.setup()
    capturePreview.mockResolvedValue({
      proposed: [
        {
          domain: 'values',
          label: 'honest_relationships',
          value: 'Honest relationships matter most to me.',
          elaboration: null,
          mutability: 'stable',
          confidence: 0.8,
        },
      ],
    })
    capture.mockResolvedValue({ attributes_saved: 1 })

    renderWithProviders(<TeachTab bootstrapQuery={createBootstrapQuery()} />, {
      appState: { backend: 'external' },
    })

    await user.type(
      screen.getByPlaceholderText(/share something useful in a few sentences\./i),
      'Honest relationships matter most to me.',
    )

    const saveNoteButton = screen.getByRole('button', { name: 'Save note' })
    expect(saveNoteButton).toBeDisabled()

    await user.click(
      screen.getByLabelText(
        /i understand this raw note may be sent to my configured external provider for extraction\./i,
      ),
    )

    expect(saveNoteButton).toBeEnabled()
    await user.click(saveNoteButton)

    await waitFor(() => {
      expect(capturePreview).toHaveBeenCalledWith(
        'Honest relationships matter most to me.',
        'values',
        true,
      )
    })
    await waitFor(() => {
      expect(capture).toHaveBeenCalledWith(
        'Honest relationships matter most to me.',
        'values',
        [
          {
            domain: 'values',
            label: 'honest_relationships',
            value: 'Honest relationships matter most to me.',
            elaboration: null,
            mutability: 'stable',
            confidence: 0.8,
          },
        ],
        true,
      )
    })
  })
})
