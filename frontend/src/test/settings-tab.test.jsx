import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import SettingsTab from '../components/settings/SettingsTab.jsx'
import TeachTab from '../components/teach/TeachTab.jsx'
import {
  analyzeArtifact,
  promoteArtifact,
  updateSecurityCheckOverride,
  uploadArtifact,
} from '../api/endpoints.js'
import { renderWithProviders } from './renderWithProviders.jsx'

vi.mock('../api/endpoints.js', () => ({
  analyzeArtifact: vi.fn(),
  answerTeachQuestion: vi.fn(),
  capture: vi.fn(),
  capturePreview: vi.fn(),
  feedbackTeachQuestion: vi.fn(),
  getArtifactAnalysis: vi.fn(),
  promoteArtifact: vi.fn(),
  saveProviderCredentials: vi.fn(),
  saveSetupProfile: vi.fn(),
  updateSecurityCheckOverride: vi.fn(),
  uploadArtifact: vi.fn(),
}))

function createBootstrapData(overrides = {}) {
  return {
    onboarding_completed: true,
    cards: [
      {
        title: 'Teach the engine',
        body: 'Share helpful information at your own pace.',
      },
    ],
    privacy_preference: 'balanced',
    privacy_preferences: [
      {
        code: 'balanced',
        label: 'Balanced',
        description: 'Balance privacy and capability.',
      },
    ],
    active_profile: 'balanced_hybrid',
    preferred_provider: 'anthropic',
    preferred_backend: 'local',
    profiles: [
      {
        code: 'balanced_hybrid',
        label: 'Balanced hybrid',
        description: 'Use a mix of local and external support.',
        recommendation_reason: 'Good default for mixed setups.',
        provider_scope: 'hybrid',
        default_backend: 'external',
        provider_options: ['anthropic'],
        recommended_provider: 'anthropic',
        available: true,
        requires_external_provider: true,
      },
    ],
    providers: [
      {
        provider: 'ollama',
        label: 'Local model',
        deployment: 'local',
        trust_boundary: 'self_hosted',
        available: true,
        is_local: true,
        auth_strategy: 'none',
        credential_fields: [],
      },
      {
        provider: 'anthropic',
        label: 'Anthropic',
        deployment: 'external',
        trust_boundary: 'external',
        available: true,
        auth_strategy: 'api_key',
        credential_fields: [{ name: 'api_key', label: 'API key', secret: true }],
      },
    ],
    security_posture: {
      supported: true,
      platform: 'macos',
      checks: [
        {
          code: 'personal_recovery_key',
          label: 'Personal recovery key',
          status: 'unknown',
          recommended_value: 'Enabled.',
          action_required: true,
          user_marked_complete: false,
          summary: 'A personal recovery key keeps recovery under your control.',
          recommendation: 'Prefer a personal/local recovery key over shared recovery.',
        },
      ],
    },
    questions: [
      {
        id: 'question-1',
        prompt: 'What matters most to you right now?',
        domain: 'values',
        source: 'catalog',
        intent_key: 'values_what_matters_most_to_you_right_now',
        status: 'pending',
        priority: 10,
      },
    ],
    ...overrides,
  }
}

function createBootstrapQuery(data) {
  return {
    isLoading: false,
    data,
  }
}

describe('TeachTab', () => {
  it('keeps setup panels off the page after onboarding is complete', () => {
    const bootstrap = createBootstrapData()

    renderWithProviders(<TeachTab bootstrapQuery={createBootstrapQuery(bootstrap)} />, {
      appState: {
        teachState: bootstrap,
      },
    })

    expect(screen.queryByText('Privacy preference')).not.toBeInTheDocument()
    expect(screen.queryByText('Recommended configurations')).not.toBeInTheDocument()
    expect(screen.getByText('Guided question')).toBeInTheDocument()
  })

  it('uploads, analyzes, and promotes artifact candidates locally', async () => {
    const user = userEvent.setup()
    const bootstrap = createBootstrapData()
    uploadArtifact.mockResolvedValue({ artifact_id: 'artifact-1', chunk_count: 1, analysis_status: 'not_analyzed' })
    analyzeArtifact.mockResolvedValue({
      artifact_id: 'artifact-1',
      analysis_status: 'analyzed',
      summary: 'A local collection of dinner recipes the user has made.',
      descriptor_tokens: ['recipe', 'dinner', 'meal'],
      candidate_attributes: [
        {
          candidate_id: 'attribute_0_dinner_recipes',
          domain: 'patterns',
          label: 'dinner_recipes',
          value: 'The uploaded artifact tracks dinner recipes I have made.',
          elaboration: null,
          mutability: 'evolving',
          confidence: 0.7,
          status: 'pending',
        },
      ],
      candidate_preferences: [
        {
          candidate_id: 'preference_0_food_pasta',
          category: 'food',
          subject: 'pasta',
          signal: 'like',
          strength: 3,
          summary: 'Pasta dishes appear repeatedly in the recipe list.',
          status: 'pending',
        },
      ],
    })
    promoteArtifact.mockResolvedValue({
      artifact_id: 'artifact-1',
      promoted_attribute_ids: ['attr-1'],
      promoted_preference_signal_ids: ['pref-1'],
      analysis: {
        artifact_id: 'artifact-1',
        analysis_status: 'analyzed',
        summary: 'A local collection of dinner recipes the user has made.',
        descriptor_tokens: ['recipe', 'dinner', 'meal'],
        candidate_attributes: [
          {
            candidate_id: 'attribute_0_dinner_recipes',
            domain: 'patterns',
            label: 'dinner_recipes',
            value: 'The uploaded artifact tracks dinner recipes I have made.',
            elaboration: null,
            mutability: 'evolving',
            confidence: 0.7,
            status: 'promoted',
          },
        ],
        candidate_preferences: [
          {
            candidate_id: 'preference_0_food_pasta',
            category: 'food',
            subject: 'pasta',
            signal: 'like',
            strength: 3,
            summary: 'Pasta dishes appear repeatedly in the recipe list.',
            status: 'promoted',
          },
        ],
      },
    })

    renderWithProviders(<TeachTab bootstrapQuery={createBootstrapQuery(bootstrap)} />, {
      appState: {
        teachState: bootstrap,
      },
    })

    await user.type(screen.getByPlaceholderText('Document title'), 'Dinner recipes')
    await user.type(screen.getByPlaceholderText('Tags, comma separated'), 'recipes,dinner')
    await user.type(screen.getByPlaceholderText('Paste text here, or choose a file below.'), 'Lasagna\nTikka masala\nPasta bake')
    await user.click(screen.getByRole('button', { name: 'Save upload' }))

    await waitFor(() => {
      expect(uploadArtifact).toHaveBeenCalled()
      expect(analyzeArtifact).toHaveBeenCalledWith('artifact-1')
    })

    expect(await screen.findByText(/local collection of dinner recipes/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Promote selected' }))

    await waitFor(() => {
      expect(promoteArtifact).toHaveBeenCalledWith('artifact-1', {
        selected_attributes: [
          expect.objectContaining({ candidate_id: 'attribute_0_dinner_recipes' }),
        ],
        selected_preferences: [
          expect.objectContaining({ candidate_id: 'preference_0_food_pasta' }),
        ],
      })
    })
  })
})

describe('SettingsTab', () => {
  it('lets the user mark unknown security checks complete', async () => {
    const user = userEvent.setup()
    const bootstrap = createBootstrapData()
    updateSecurityCheckOverride.mockResolvedValue({ checks: [] })

    renderWithProviders(<SettingsTab bootstrapQuery={createBootstrapQuery(bootstrap)} />, {
      appState: {
        teachState: bootstrap,
      },
    })

    await user.click(screen.getByRole('button', { name: 'Mark complete' }))

    await waitFor(() => {
      expect(updateSecurityCheckOverride).toHaveBeenCalledWith('personal_recovery_key', {
        completed: true,
      })
    })
  })
})
