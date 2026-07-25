import WelcomeItem from './WelcomeItem'
import DocumentationIcon from './icons/IconDocumentation'
import ToolingIcon from './icons/IconTooling'
import EcosystemIcon from './icons/IconEcosystem'
import CommunityIcon from './icons/IconCommunity'
import styles from './TheWelcome.module.css'

export default function TheWelcome() {
  return (
    <div className={styles.container}>
      <div className={styles.welcomeSection}>
        <h1>Welcome to OpenPosture</h1>
        <p className={styles.description}>
          OpenPosture is a revolutionary app designed to transform your posture habits, offering a
          comprehensive suite of features tailored to meet your needs. With OpenPosture, you’ll
          experience personalized guidance and real-time feedback to correct poor seated posture,
          enhance spinal alignment, and alleviate discomfort.
        </p>
      </div>
      <div className={styles.steps}>
        <WelcomeItem icon={<DocumentationIcon />} heading="Dataset Preparation">
          A curated dataset of diverse seated posture images underpins model training, focusing on
          critical body parts like the back, hands, neck, and feet for accurate posture recognition.
        </WelcomeItem>

        <WelcomeItem icon={<ToolingIcon />} heading="Posture Analysis">
          The system precisely identifies key posture points and assesses posture alignment,
          pinpointing specific areas needing improvement.
        </WelcomeItem>

        <WelcomeItem icon={<EcosystemIcon />} heading="Personalized Feedback">
          Based on posture analysis, personalized correction recommendations are generated and
          delivered through this React UI, powered by Flask and Firebase, offering users an
          intuitive and seamless experience in improving their posture.
        </WelcomeItem>

        <WelcomeItem icon={<CommunityIcon />} heading="Feedback Mechanism">
          An immediate feedback mechanism provides visual cues directly to the user, suggesting
          posture adjustments, prompts breaks, or offering specific exercises designed to mitigate
          any detected posture issues, fostering an interactive and beneficial user experience.
        </WelcomeItem>
      </div>
    </div>
  )
}
