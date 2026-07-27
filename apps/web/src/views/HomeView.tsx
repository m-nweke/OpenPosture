import { Link } from 'react-router-dom'
import { useAuth } from '../auth'
import logo from '../assets/openPose.png'
import styles from './HomeView.module.css'

const cx = (...names: Array<string | false | undefined>) => names.filter(Boolean).join(' ')

/**
 * The landing page.
 *
 * Replaces `TheWelcome`, which was the Vite starter template's four-panel grid with its icons
 * and headings edited. It described the project in the future tense — "will analyse", "will
 * provide" — because when it was written none of it worked.
 *
 * Everything claimed below is now true and checkable, which is the only reason it is worth
 * saying: the gaps paragraph in particular is the behaviour the whole rules engine exists for.
 */
export default function HomeView() {
  const { user } = useAuth()

  return (
    <>
      <section className={cx(styles.hero)}>
        <div className={cx(styles.heroText)}>
          <p className={cx(styles.eyebrow)}>Posture analysis from a single photograph</p>
          <h1>
            Find out what your posture is actually doing: <em>measured</em>, not guessed.
          </h1>
          <p className={cx(styles.lede)}>
            Upload a photo of yourself sitting, taken from the side. A pose model finds your joints,
            and a rules engine turns them into angles, plain-language findings, and an honest
            account of anything it could not see.
          </p>
          <div className={cx(styles.actions)}>
            <Link className={cx('button', 'buttonPrimary')} to={user ? '/dashboard' : '/register'}>
              {user ? 'Go to your dashboard' : 'Get started'}
            </Link>
            {!user && (
              <Link className={cx('button', 'buttonSecondary')} to="/login">
                I already have an account
              </Link>
            )}
          </div>
        </div>

        {/* Decorative: the heading beside it already says what the product is, so announcing
            "OpenPosture logo" would only repeat it to a screen reader. */}
        <img alt="" aria-hidden="true" className={cx(styles.heroLogo)} src={logo} />
      </section>

      <section className={cx(styles.features)} aria-labelledby="how-heading">
        <h2 id="how-heading" className="sr-only">
          How it works
        </h2>

        <article className={cx('card', styles.feature)}>
          <span className={cx(styles.step)} aria-hidden="true">
            1
          </span>
          <h3>Upload a side-on photo</h3>
          <p>
            JPEG, PNG or WebP, up to 10&nbsp;MB. Rotation from your phone&rsquo;s camera is handled,
            so a portrait photo is not analysed sideways.
          </p>
        </article>

        <article className={cx('card', styles.feature)}>
          <span className={cx(styles.step)} aria-hidden="true">
            2
          </span>
          <h3>Seven measurements</h3>
          <p>
            Trunk lean, forward head, elbow and knee angles, heel contact, arm folding, and whether
            the photo was lateral enough to trust the rest. Computed in world space, so distance
            from the camera does not change the answer.
          </p>
        </article>

        <article className={cx('card', styles.feature)}>
          <span className={cx(styles.step)} aria-hidden="true">
            3
          </span>
          <h3>Findings, and honest gaps</h3>
          <p>
            Anything it could not measure is named, with the reason. It will tell you it could not
            see your knees rather than quietly reporting that they are fine.
          </p>
        </article>
      </section>

      <section className={cx('card', styles.privacy)} aria-labelledby="privacy-heading">
        <h2 id="privacy-heading">Where your photo goes</h2>
        <p>
          It is sent to the API running on this machine, analysed, and stored locally. Nothing is
          sent to a third party, and no account of yours exists anywhere but this browser. The
          sign-in here is a placeholder while persistence is built.
        </p>
      </section>
    </>
  )
}
