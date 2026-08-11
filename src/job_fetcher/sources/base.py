from abc import ABC, abstractmethod

class JobSource(ABC):
    @abstractmethod
    def fetch(self, company):
        raise NotImplementedError
