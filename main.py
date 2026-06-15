from dotenv import load_dotenv
load_dotenv()

from langchain_core import __version__ as langchain_core_version
from langchain import __version__ as lg_version
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.document_loaders import TextLoader


def main():

    # model = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    # response = model.invoke("")
    # print(f"Model test response: {response}")

    loader = TextLoader("/home/tholvimaaran/doc.txt")
    documents = loader.load()
    print(f"Loaded documents: {documents}")

if __name__ == "__main__":
    main()

