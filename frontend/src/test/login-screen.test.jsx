import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import LoginScreen from '../components/auth/LoginScreen.jsx'
import { renderWithProviders } from './renderWithProviders.jsx'

function createAuth(overrides = {}) {
  return {
    isChecking: false,
    login: vi.fn(),
    ...overrides,
  }
}

describe('LoginScreen', () => {
  it('disables submit until a passphrase is entered', () => {
    renderWithProviders(<LoginScreen auth={createAuth()} />)

    expect(screen.getByRole('button', { name: 'Enter' })).toBeDisabled()
  })

  it('submits the passphrase and clears the field on success', async () => {
    const user = userEvent.setup()
    const auth = createAuth({
      login: vi.fn().mockResolvedValue({ token: 'session-token' }),
    })

    renderWithProviders(<LoginScreen auth={auth} />)

    const input = screen.getByLabelText('Passphrase')
    await user.type(input, 'correct horse')
    await user.click(screen.getByRole('button', { name: 'Enter' }))

    expect(auth.login).toHaveBeenCalledWith('correct horse')
    expect(input).toHaveValue('')
    expect(screen.getByText('Session token stays in this tab only.')).toBeInTheDocument()
  })

  it('shows an incorrect passphrase error and shake state on failure', async () => {
    const user = userEvent.setup()
    const auth = createAuth({
      login: vi.fn().mockRejectedValue({ response: { status: 401 } }),
    })

    renderWithProviders(<LoginScreen auth={auth} />)

    await user.type(screen.getByLabelText('Passphrase'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Enter' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Incorrect passphrase')
    expect(screen.getByLabelText('Passphrase')).toHaveValue('')
    expect(document.querySelector('.login-card')).toHaveClass('shake')
  })

  it('locks the form immediately after a rate-limit response', async () => {
    const user = userEvent.setup()
    const auth = createAuth({
      login: vi.fn().mockRejectedValue({ response: { status: 429 } }),
    })

    renderWithProviders(<LoginScreen auth={auth} />)

    await user.type(screen.getByLabelText('Passphrase'), 'locked')
    await user.click(screen.getByRole('button', { name: 'Enter' }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Too many attempts. Try again later.',
    )
    expect(screen.getByLabelText('Passphrase')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Enter' })).toBeDisabled()
  })

  it('locks the form after five failed attempts', async () => {
    const user = userEvent.setup()
    const auth = createAuth({
      login: vi.fn().mockRejectedValue({ response: { status: 401 } }),
    })

    renderWithProviders(<LoginScreen auth={auth} />)

    const input = screen.getByLabelText('Passphrase')
    const submit = screen.getByRole('button', { name: 'Enter' })

    for (let attempt = 0; attempt < 5; attempt += 1) {
      await user.type(input, `wrong-${attempt}`)
      await user.click(submit)
    }

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Too many attempts. Try again later.',
    )
    expect(input).toBeDisabled()
    expect(submit).toBeDisabled()
  })
})
