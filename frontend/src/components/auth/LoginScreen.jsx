import { useEffect, useState } from 'react'

export default function LoginScreen({ auth }) {
  const [passphrase, setPassphrase] = useState('')
  const [error, setError] = useState('')
  const [shake, setShake] = useState(false)
  const [failures, setFailures] = useState(0)
  const [locked, setLocked] = useState(false)

  useEffect(() => {
    if (!shake) {
      return undefined
    }

    const timeout = window.setTimeout(() => setShake(false), 350)
    return () => window.clearTimeout(timeout)
  }, [shake])

  const handleSubmit = async (event) => {
    event.preventDefault()

    if (!passphrase.trim() || locked || auth.isChecking) {
      return
    }

    try {
      setError('')
      await auth.login(passphrase)
      setPassphrase('')
      setFailures(0)
    } catch (requestError) {
      const status = requestError.response?.status
      const nextFailures = failures + 1

      setShake(true)
      setPassphrase('')

      if (status === 429 || nextFailures >= 5) {
        setLocked(true)
        setError('Too many attempts. Try again later.')
        return
      }

      setFailures(nextFailures)
      setError('Incorrect passphrase')
    }
  }

  return (
    <section className="login-screen">
      <div className={`login-card ${shake ? 'shake' : ''}`}>
        <p className="eyebrow">Private access</p>
        <h1 className="login-title">identity engine</h1>
        <form className="login-form" onSubmit={handleSubmit}>
          <div>
            <label className="field-label" htmlFor="passphrase">
              Passphrase
            </label>
            <input
              id="passphrase"
              type="password"
              value={passphrase}
              onChange={(event) => setPassphrase(event.target.value)}
              placeholder="Enter your passphrase"
              autoComplete="current-password"
              disabled={locked}
            />
          </div>
          {error ? (
            <p className="field-error" role="alert">
              {error}
            </p>
          ) : (
            <p className="field-help">Session token stays in this tab only.</p>
          )}
          <button
            type="submit"
            className="button-primary"
            disabled={!passphrase.trim() || locked || auth.isChecking}
          >
            Enter
          </button>
        </form>
      </div>
    </section>
  )
}
