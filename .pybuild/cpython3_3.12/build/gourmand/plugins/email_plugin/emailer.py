import subprocess

from gourmand.gdebug import debug


class Emailer:
    def __init__ (self, emailaddress=None, subject=None, body=None, attachments=[]):
        self.emailaddress=None
        self.subject=subject
        self.body=body
        self.attachments=attachments

    def send_email (self):
        build_command = ["xdg-email"] 
        if self.subject:
            build_command.extend(["--subject", self.subject])
        if self.body:
            build_command.extend(['--body', self.body])
        for a in self.attachments:
            print('Adding attachment', a)
            build_command.extend(['--attach', a])
        #print (''.join(str(build_command)))
        subprocess.run(build_command)
