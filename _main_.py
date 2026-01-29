from engine.services.beget_mail_controller import BegetMailController


def main():
    beget_mail_controller = BegetMailController()
    beget_mail_controller.process_incoming_emails()


if __name__ == "__main__":
    main()
