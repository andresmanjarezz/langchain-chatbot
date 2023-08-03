import os
import pinecone
import requests
import mimetypes
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlsplit
from dotenv import load_dotenv
from langchain.embeddings import OpenAIEmbeddings
from langchain.docstore.document import Document

from langchain.vectorstores import Pinecone as BasePinecone
from langchain.chat_models import ChatOpenAI
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import (
    AIMessage,
    HumanMessage,
    SystemMessage
)
from langchain.document_loaders import (
    PyPDFLoader,
    CSVLoader,
    UnstructuredWordDocumentLoader,
    WebBaseLoader,
    TextLoader,
)
from langchain.retrievers import ContextualCompressionRetriever
from langchain.retrievers.document_compressors import LLMChainExtractor
import uuid

from typing import (
    Any,
    List,
    Optional,
    Type,
    TypeVar,
    Tuple,
    Iterable
)
from langchain.embeddings.base import Embeddings

VST = TypeVar("VST", bound="VectorStore")

load_dotenv()

# Get the Variables from the .env file
OPENAI_API_KEY = os.getenv('OPEN_AI_KEY')
PINECONE_API_KEY = os.getenv('PINECONE_API_KEY')
PINECONE_ENVIRONMENT = os.getenv('PINECONE_ENVIRONMENT')
PINECONE_INDEX_NAME = os.getenv('PINECONE_INDEX_NAME')
# WEBSITE_URL = os.getenv('WEBSITE_URLS')
# WEBSITE_URLS = WEBSITE_URL.split(",")

embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
chat = ChatOpenAI(temperature=0, openai_api_key=OPENAI_API_KEY)

compressor = LLMChainExtractor.from_llm(chat)


class Pinecone(BasePinecone):
    def add_texts(
            self,
            texts: Iterable[str],
            metadatas: Optional[List[dict]] = None,
            ids: Optional[List[str]] = None,
            namespace: Optional[str] = None,
            batch_size: int = 32,
            **kwargs: Any,
    ) -> List[str]:
        """Run more texts through the embeddings and add to the vectorstore.

        Args:
            texts: Iterable of strings to add to the vectorstore.
            metadatas: Optional list of metadatas associated with the texts.
            ids: Optional list of ids to associate with the texts.
            namespace: Optional pinecone namespace to add the texts to.

        Returns:
            List of ids from adding the texts into the vectorstore.

        """
        if namespace is None:
            namespace = self._namespace
        # Embed and create the documents
        docs = []
        ids = ids or [str(uuid.uuid4()) for _ in texts]
        for i, text in enumerate(texts):
            embedding = self._embedding_function(text)
            metadata = metadatas[i] if metadatas else {}
            metadata[self._text_key] = text
            docs.append((ids[i], embedding, metadata))
        # upsert to Pinecone
        upsert_result = self._index.upsert(
            vectors=docs, namespace=namespace, batch_size=batch_size, **kwargs
        )
        print("Upserted vector IDs:", list(upsert_result.keys()))
        return ids

    @classmethod
    def from_documents(
            cls,
            documents: List[Document],
            embedding: Embeddings,
            batch_size: int = 32,
            text_key: str = "text",
            index_name: Optional[str] = None,
            namespace: Optional[str] = None,
            upsert_kwargs: Optional[dict] = None,
            **kwargs: Any,
    ) -> BasePinecone:
        """Construct Pinecone wrapper from raw documents.

        This is a user friendly interface that:
            1. Embeds documents.
            2. Adds the documents to a provided Pinecone index

        This is intended to be a quick way to get started.

        Example:
            .. code-block:: python

                from langchain import Pinecone
                from langchain.embeddings import OpenAIEmbeddings
                import pinecone

                # The environment should be the one specified next to the API key
                # in your Pinecone console
                pinecone.init(api_key="***", environment="...")
                embeddings = OpenAIEmbeddings()
                pinecone = Pinecone.from_texts(
                    texts,
                    embeddings,
                    index_name="langchain-demo"
                )
        """
        texts = [doc.page_content for doc in documents]
        metadatas = [doc.metadata for doc in documents]
        ids = [doc.id for doc in documents]
        return cls.from_texts(
            texts,
            embedding,
            metadatas=metadatas,
            ids=ids,
            batch_size=batch_size,
            text_key=text_key,
            index_name=index_name,
            namespace=namespace,
            upsert_kwargs=upsert_kwargs,
            **kwargs,
        )


class PineconeManager:
    def __init__(self, api_key, environment):
        pinecone.init(
            api_key=api_key,
            environment=environment
        )

    def list_indexes(self):
        return pinecone.list_indexes()

    def create_index(self, index_name, dimension, metric):
        pinecone.create_index(name=index_name, dimension=dimension, metric=metric)

    def delete_index(self, index_name):
        pinecone.deinit()


class URLHandler:
    @staticmethod
    def is_valid_url(url):
        parsed_url = urlsplit(url)
        return bool(parsed_url.scheme) and bool(parsed_url.netloc)

    @staticmethod
    def extract_links(url):
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')

        links = []
        for link in soup.find_all('a'):
            href = link.get('href')
            if href:
                absolute_url = urljoin(url, href)
                if URLHandler.is_valid_url(absolute_url):
                    links.append(absolute_url)

        return links

    @staticmethod
    def extract_links_from_websites(websites):
        all_links = []

        for website in websites:
            links = URLHandler.extract_links(website)
            all_links.extend(links)

        return all_links


class DocumentLoaderFactory:
    @staticmethod
    def get_loader(file_path_or_url):
        if file_path_or_url.startswith("http://") or file_path_or_url.startswith("https://"):
            handle_website = URLHandler()
            return WebBaseLoader(handle_website.extract_links_from_websites([file_path_or_url]))
        else:
            mime_type, _ = mimetypes.guess_type(file_path_or_url)

            if mime_type == 'application/pdf':
                return PyPDFLoader(file_path_or_url)
            elif mime_type == 'text/csv':
                return CSVLoader(file_path_or_url)
            elif mime_type == 'text/plain':
                return TextLoader(file_path_or_url)
            elif mime_type in ['application/msword',
                               'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
                return UnstructuredWordDocumentLoader(file_path_or_url)
            else:
                raise ValueError(f"Unsupported file type: {mime_type}")


class PineconeIndexManager:
    def __init__(self, pinecone_manager, index_name):
        self.pinecone_manager = pinecone_manager
        self.index_name = index_name

    def index_exists(self):
        active_indexes = self.pinecone_manager.list_indexes()
        return self.index_name in active_indexes

    def create_index(self, dimension, metric):
        self.pinecone_manager.create_index(self.index_name, dimension, metric)

    def delete_index(self):
        self.pinecone_manager.delete_index(self.index_name)


def train_or_load_model(train, pinecone_index_manager, file_path, name_space):
    if train:
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=5000,
                                                       chunk_overlap=400)

        loader = DocumentLoaderFactory.get_loader(file_path)
        pages = loader.load_and_split(text_splitter=text_splitter)

        if pinecone_index_manager.index_exists():
            print("Updating the model")
            pinecone_index = Pinecone.from_documents(pages, embeddings,
                                                     index_name=pinecone_index_manager.index_name,
                                                     namespace=name_space)
            # print(vector_ids)

            return pinecone_index
    else:
        pinecone_index = Pinecone.from_existing_index(index_name=pinecone_index_manager.index_name,
                                                      namespace=name_space, embedding=embeddings)
        return pinecone_index


def answer_questions(pinecone_index):
    pinecone_index_retriever = pinecone_index.as_retriever()
    messages = [
        SystemMessage(
            content='You will be provided with a document delimited by triple quotes and a question. Your task is to '
                    'answer the question using only the provided document and to cite the passage(s) of the document '
                    'used to answer the question. If the document does not contain the information needed to answer '
                    'this question then simply write: "Insufficient information." If an answer to the question is '
                    'provided, it must be annotated with a citation. Use the following format for to cite relevant '
                    'passages ({"citation": …}).')
    ]
    while True:
        question = input("Ask a question (type 'stop' to end): ")
        if question.lower() == "stop":
            break

        compression_retriever = ContextualCompressionRetriever(base_compressor=compressor,
                                                               base_retriever=pinecone_index_retriever)

        # docs = pinecone_index_retriever.similarity_search(query=question, k=1)
        docs = compression_retriever.get_relevant_documents(query=question)
        main_content = '"""'
        for doc in docs:
            main_content += doc.page_content + "\n\n"

        main_content += '"""\n\n\nQuestion: ' + question + "\n"
        print(main_content)

        messages.append(HumanMessage(content=main_content))
        ai_response = chat(messages).content
        messages.pop()
        messages.append(HumanMessage(content=question))
        messages.append(AIMessage(content=ai_response))

        print(ai_response)


def main():
    pinecone_manager = PineconeManager(PINECONE_API_KEY, PINECONE_ENVIRONMENT)
    pinecone_index_manager = PineconeIndexManager(pinecone_manager, PINECONE_INDEX_NAME)
    file_path = "data/shams.txt"
    name_space = "test-3"

    train = int(input("Do you want to train the model? (1 for yes, 0 for no): "))
    pinecone_index = train_or_load_model(train, pinecone_index_manager, file_path, name_space)
    answer_questions(pinecone_index)


if __name__ == "__main__":
    main()
