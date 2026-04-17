import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import AttributeEditor from '../components/graph/AttributeEditor.jsx'
import { createAttribute, createDomain } from './fixtures.js'
import { renderWithProviders } from './renderWithProviders.jsx'
import {
  createAttribute as createAttributeRequest,
  retractAttribute,
  updateAttribute,
} from '../api/endpoints.js'

vi.mock('../api/endpoints.js', () => ({
  createAttribute: vi.fn(),
  retractAttribute: vi.fn(),
  updateAttribute: vi.fn(),
}))

const domains = [
  createDomain({ domain: 'personality' }),
  createDomain({ domain: 'fears' }),
]

describe('AttributeEditor', () => {
  it('validates required fields while creating', async () => {
    const user = userEvent.setup()

    renderWithProviders(
      <AttributeEditor
        attribute={{ domain: 'personality', label: '', value: '' }}
        domains={domains}
        isOpen
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Value is required.')

    await user.type(screen.getByLabelText('Value'), 'Something true')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Label is required.')
  })

  it('creates attributes with trimmed values and shows the external warning', async () => {
    const user = userEvent.setup()
    const onSaved = vi.fn().mockResolvedValue(undefined)
    createAttributeRequest.mockResolvedValue({ id: 'new-attribute' })

    renderWithProviders(
      <AttributeEditor
        attribute={{ domain: 'personality', label: '', value: '' }}
        domains={domains}
        isOpen
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    )

    await user.type(screen.getByLabelText('Label'), ' response_to_uncertainty ')
    await user.type(screen.getByLabelText('Value'), ' Needs space to think ')
    await user.click(screen.getByRole('button', { name: 'external ok' }))

    expect(
      screen.getByText(
        'This attribute may be included in prompts sent to external APIs when external mode is active.',
      ),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(createAttributeRequest).toHaveBeenCalledWith({
        domain: 'personality',
        label: 'response_to_uncertainty',
        value: 'Needs space to think',
        elaboration: null,
        confidence: 0.5,
        mutability: 'evolving',
        routing: 'external_ok',
        source: 'explicit',
      })
    })

    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('forces protected domains back to local-only routing', async () => {
    const user = userEvent.setup()
    createAttributeRequest.mockResolvedValue({ id: 'new-attribute' })

    renderWithProviders(
      <AttributeEditor
        attribute={{
          domain: 'fears',
          label: '',
          value: '',
          routing: 'external_ok',
        }}
        domains={domains}
        isOpen
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'external ok' })).toBeDisabled()

    await user.type(screen.getByLabelText('Label'), 'core_fear')
    await user.type(screen.getByLabelText('Value'), 'Being exposed')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(createAttributeRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          routing: 'local_only',
          domain: 'fears',
        }),
      )
    })
  })

  it('surfaces save errors while editing existing attributes', async () => {
    const user = userEvent.setup()
    updateAttribute.mockRejectedValue({
      response: { data: { message: 'Only one active label is allowed.' } },
    })

    renderWithProviders(
      <AttributeEditor
        attribute={createAttribute({ id: 'attribute-1', label: 'honesty' })}
        domains={domains}
        isOpen
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Only one active label is allowed.',
    )
  })

  it('supports retract confirm/cancel paths and backdrop close', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    const onSaved = vi.fn().mockResolvedValue(undefined)
    const confirm = vi.spyOn(window, 'confirm')
    retractAttribute.mockResolvedValue({})

    const { container, rerender } = renderWithProviders(
      <AttributeEditor
        attribute={createAttribute({ id: 'attribute-1' })}
        domains={domains}
        isOpen
        onClose={onClose}
        onSaved={onSaved}
      />,
    )

    confirm.mockReturnValue(false)
    await user.click(screen.getByRole('button', { name: 'Retract' }))
    expect(retractAttribute).not.toHaveBeenCalled()

    confirm.mockReturnValue(true)
    await user.click(screen.getByRole('button', { name: 'Retract' }))

    await waitFor(() => {
      expect(retractAttribute).toHaveBeenCalledWith('attribute-1')
    })
    expect(onSaved).toHaveBeenCalledTimes(1)

    rerender(
      <AttributeEditor
        attribute={createAttribute({ id: 'attribute-1' })}
        domains={domains}
        isOpen
        onClose={onClose}
        onSaved={onSaved}
      />,
    )

    await user.click(container.querySelector('.editor-backdrop'))
    expect(onClose).toHaveBeenCalled()
  })
})
