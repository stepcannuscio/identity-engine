import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import CapturePanel from '../components/graph/CapturePanel.jsx'
import { createDomain } from './fixtures.js'
import { renderWithProviders } from './renderWithProviders.jsx'
import { capture, capturePreview } from '../api/endpoints.js'

vi.mock('../api/endpoints.js', () => ({
  capture: vi.fn(),
  capturePreview: vi.fn(),
}))

const domains = [
  createDomain({ domain: 'personality' }),
  createDomain({ domain: 'values' }),
]

describe('CapturePanel', () => {
  it('opens and validates preview input', async () => {
    const user = userEvent.setup()

    renderWithProviders(<CapturePanel domains={domains} onSaved={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /\+ Quick capture/i }))
    await user.click(screen.getByRole('button', { name: 'Preview' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Enter a note to preview.')
  })

  it('shows a preview error when extraction fails', async () => {
    const user = userEvent.setup()
    capturePreview.mockRejectedValue(new Error('preview failed'))

    renderWithProviders(<CapturePanel domains={domains} onSaved={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /\+ Quick capture/i }))
    await user.type(screen.getByLabelText("What's on your mind?"), 'A note')
    await user.click(screen.getByRole('button', { name: 'Preview' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Unable to preview capture right now.',
    )
  })

  it('lets the user reject preview items and disables saving when none are accepted', async () => {
    const user = userEvent.setup()
    capturePreview.mockResolvedValue({
      proposed: [
        {
          domain: 'values',
          label: 'honesty',
          value: 'Honesty matters',
          elaboration: 'Especially in close relationships.',
          confidence: 0.8,
          mutability: 'stable',
        },
      ],
    })

    renderWithProviders(<CapturePanel domains={domains} onSaved={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /\+ Quick capture/i }))
    await user.type(screen.getByLabelText("What's on your mind?"), 'A note about honesty')
    await user.click(screen.getByRole('button', { name: 'Preview' }))

    expect(await screen.findByText('Honesty matters')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Reject' }))

    expect(screen.getByRole('button', { name: 'Save accepted' })).toBeDisabled()
  })

  it('saves accepted attributes and resets the form', async () => {
    const user = userEvent.setup()
    const onSaved = vi.fn().mockResolvedValue(undefined)
    capturePreview.mockResolvedValue({
      proposed: [
        {
          domain: 'values',
          label: 'honesty',
          value: 'Honesty matters',
          elaboration: 'Especially in close relationships.',
          confidence: 0.8,
          mutability: 'stable',
        },
      ],
    })
    capture.mockResolvedValue({ attributes_saved: 1 })

    renderWithProviders(<CapturePanel domains={domains} onSaved={onSaved} />)

    await user.click(screen.getByRole('button', { name: /\+ Quick capture/i }))
    await user.type(screen.getByLabelText("What's on your mind?"), 'A note about honesty')
    await user.selectOptions(screen.getByRole('combobox'), 'values')
    await user.click(screen.getByRole('button', { name: 'Preview' }))
    await user.click(await screen.findByRole('button', { name: 'Save accepted' }))

    await waitFor(() => {
      expect(capture).toHaveBeenCalledWith('A note about honesty', 'values', [
        {
          domain: 'values',
          label: 'honesty',
          value: 'Honesty matters',
          elaboration: 'Especially in close relationships.',
          mutability: 'stable',
          confidence: 0.8,
        },
      ])
    })

    expect(onSaved).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Saved 1 attribute(s)')).toBeInTheDocument()
    expect(screen.queryByLabelText("What's on your mind?")).not.toBeInTheDocument()
  })

  it('shows a save error when persistence fails', async () => {
    const user = userEvent.setup()
    capturePreview.mockResolvedValue({
      proposed: [
        {
          domain: 'personality',
          label: 'reflection_style',
          value: 'Reflective',
          elaboration: '',
          confidence: 0.7,
          mutability: 'evolving',
        },
      ],
    })
    capture.mockRejectedValue(new Error('save failed'))

    renderWithProviders(<CapturePanel domains={domains} onSaved={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /\+ Quick capture/i }))
    await user.type(screen.getByLabelText("What's on your mind?"), 'A reflective note')
    await user.click(screen.getByRole('button', { name: 'Preview' }))
    await user.click(await screen.findByRole('button', { name: 'Save accepted' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Unable to save accepted attributes.',
    )
  })
})
