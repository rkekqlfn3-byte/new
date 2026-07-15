class ProviderBase:
    def __init__(self, timeout, decoder):
        self.timeout = timeout
        self.decoder = decoder

    def configure(self, timeout, decoder):
        self.timeout = timeout
        self.decoder = decoder

    def decode_command_content(self, content):
        return self.decoder(content)
